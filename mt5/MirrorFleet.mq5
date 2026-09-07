//+------------------------------------------------------------------+
//|                                                  MirrorFleet.mq5 |
//|                    MirrorFleet - the MT5 bridge Expert Advisor   |
//+------------------------------------------------------------------+
// Connects this MT5 account to MirrorFleet. Every InpPollMs the EA POSTs
// its book (positions, orders, new deals, acks) to {InpServer}/api/mt5/sync
// and executes the tab-separated commands in the reply; once per start it
// says hello with the account and its symbols. It only ever reports and
// acknowledges: the key in InpKey identifies the account, nothing else is
// configured here and nothing secret is in this file.
//
// Hedging and netting accounts are both supported, as follower or master.
// The margin mode goes out in the hello; on a netting account an open may
// grow, reduce, close or flip the symbol's single net position, and the
// server works out which from the ack and the deal.
//
// Wire format: docs/superpowers/plans/2026-09-07-mt5-bridge-interfaces.md
// (sections 2 and 5), mirrored by copier/src/copier/mt5/protocol.py.
#property copyright   "MirrorFleet"
#property link        "https://mirrorfleet.com"
#property version     "1.00"
#property description "Reports this account to MirrorFleet and executes the copier's commands."

#include <Trade/Trade.mqh>

//---- inputs (the one-time key dialog on the Accounts page names these)
input string InpKey             = "";                        // MirrorFleet key (mt5_...)
input string InpServer          = "https://mirrorfleet.com"; // MirrorFleet server (allow it in Tools > Options > Expert Advisors)
input int    InpPollMs          = 250;                       // Poll interval, ms (min 100)
input ulong  InpMagic           = 20260907;                  // Magic number on copies
input int    InpDeviationPoints = 50;                        // Max slippage, points

//---- the contract (interfaces.md section 5; protocol.py constants)
#define EA_VERSION          "1.0.0"
#define PROTOCOL_VERSION    1
#define KEY_PREFIX          "mt5_"
#define HELLO_PATH          "/api/mt5/hello"
#define SYNC_PATH           "/api/mt5/sync"
#define KEY_HEADER          "X-MirrorFleet-Key"
#define HTTP_TIMEOUT_MS     3000
#define MIN_POLL_MS         100
#define RETRY_POLL_MS       2000
#define MAX_DEALS_PER_SYNC  200
#define MAX_SYNC_BODY_BYTES 245760   // the api caps sync at 256 KB; deals stop before this
#define SYMBOLS_PER_HELLO   300
#define DEAL_LOOKBACK_S     3600     // HistorySelect from the watermark's time minus this
#define ACK_FILE_LIMIT      500
#define ACK_DIR             "MirrorFleet"
#define ACK_EXT             ".acks"

//---- trade retcodes the EA reports for its own pre-checks
#define RC_INVALID          10013    // TRADE_RETCODE_INVALID
#define RC_POSITION_CLOSED  10036    // TRADE_RETCODE_POSITION_CLOSED

//---- states
#define ST_HELLO    0
#define ST_RUNNING  1
#define ST_STOPPED  2

//---- globals
CTrade   g_trade;
int      g_state           = ST_HELLO;
string   g_server          = "";       // InpServer without a trailing slash
long     g_login           = 0;
bool     g_hedging         = false;
int      g_hello_chunk     = 1;        // the next chunk to send, 1-based
int      g_hello_chunks    = 1;
string   g_hello_symbols[];            // tradable symbols, fixed for one hello round
long     g_seq             = 0;
ulong    g_watermark       = 0;        // the last deal ticket the server has
datetime g_watermark_time  = 0;        // its server time; 0 = unknown, scan all history
long     g_server_offset_s = 0;        // trade server clock minus GMT, rounded to 30 min
int      g_poll_ms         = 250;
bool     g_dirty           = false;
string   g_last_error      = "";
datetime g_last_sync       = 0;
string   g_pending_acks[];             // ack objects waiting for an OK, oldest first
ulong    g_done_ids[];                 // executed command ids, oldest first (memory + file)
string   g_done_acks[];                // their ack objects, same order

//+------------------------------------------------------------------+
//| JSON building (the EA never parses JSON beyond one key scan)     |
//+------------------------------------------------------------------+
// A JSON string literal with quotes, backslashes and control characters
// escaped. A tab or a line break inside a comment must not reach the wire
// unescaped: the reply format has no escaping of its own.
string JStr(const string value)
{
   string out = "\"";
   int n = StringLen(value);
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(value, i);
      if(c == '"')       out += "\\\"";
      else if(c == '\\') out += "\\\\";
      else if(c == '\n') out += "\\n";
      else if(c == '\r') out += "\\r";
      else if(c == '\t') out += "\\t";
      else if(c < 32)    out += StringFormat("\\u%04x", c);
      else               out += ShortToString(c);
   }
   return out + "\"";
}

// A number with exactly `digits` decimals; NaN/inf become 0 so one bad
// double can never turn the whole report into a 400.
string JNum(const double value, const int digits)
{
   if(!MathIsValidNumber(value)) return "0";
   return DoubleToString(value, digits);
}

// A number with up to `digits` decimals and no trailing zeros: 0.01, 1, 0.005.
string JTrim(const double value, const int digits)
{
   if(!MathIsValidNumber(value)) return "0";
   string s = DoubleToString(value, digits);
   int dot = StringFind(s, ".");
   if(dot < 0) return s;
   int end = StringLen(s);
   while(end > dot + 1 && StringGetCharacter(s, end - 1) == '0') end--;
   if(end == dot + 1) end = dot;
   return StringSubstr(s, 0, end);
}

// Lots as the copier wants them: a float in lots (it converts to centilots).
string JLots(const double lots) { return JTrim(lots, 8); }

string JBool(const bool value) { return value ? "true" : "false"; }

// One "name":rawValue member; the caller supplies an already-encoded value.
string JField(const string name, const string raw) { return "\"" + name + "\":" + raw; }

// A ticket field: 0 is "none", which the copier reads as null either way.
string JTicket(const ulong ticket) { return ticket == 0 ? "null" : (string)ticket; }

// The integer value of "key" in a small flat JSON object, or `fallback`.
// The only JSON the EA reads: the hello reply {"last_deal_ticket": N} and
// the api's {"status", "reason", "retry_ms"} refusals.
long JsonLong(const string text, const string key, const long fallback)
{
   int at = StringFind(text, "\"" + key + "\"");
   if(at < 0) return fallback;
   at = StringFind(text, ":", at);
   if(at < 0) return fallback;
   at++;
   int n = StringLen(text);
   while(at < n && StringGetCharacter(text, at) == ' ') at++;
   int end = at;
   while(end < n)
   {
      ushort c = StringGetCharacter(text, end);
      if((c < '0' || c > '9') && c != '-') break;
      end++;
   }
   if(end == at) return fallback;
   return StringToInteger(StringSubstr(text, at, end - at));
}

// The string value of "key" in a small flat JSON object, or `fallback`
// (reasons carry no escaped quotes).
string JsonString(const string text, const string key, const string fallback)
{
   int at = StringFind(text, "\"" + key + "\"");
   if(at < 0) return fallback;
   at = StringFind(text, ":", at);
   if(at < 0) return fallback;
   int open = StringFind(text, "\"", at + 1);
   if(open < 0) return fallback;
   int close = StringFind(text, "\"", open + 1);
   if(close < 0) return fallback;
   return StringSubstr(text, open + 1, close - open - 1);
}

//+------------------------------------------------------------------+
//| Log, chart comment, state changes                                 |
//+------------------------------------------------------------------+
void Log(const string text) { Print("MirrorFleet: ", text); }

// The chart comment always starts "MirrorFleet - "; the install steps on
// the Accounts page tell the owner to wait for "MirrorFleet - connected".
void ShowStatus(const string text) { Comment("MirrorFleet - " + text); }

// The server or the key said no: stop polling and say why. The owner fixes
// the cause and re-attaches the EA (changing an input re-initialises it).
void Stop(const string reason)
{
   g_state = ST_STOPPED;
   EventKillTimer();
   Log("stopped: " + reason);
   ShowStatus("stopped: " + reason + " (fix the cause, then re-attach the EA)");
}

// A request failed or the copier is away: show it, poll slowly until a
// good answer comes back.
void Retrying(const string error)
{
   g_last_error = error;
   g_poll_ms = RETRY_POLL_MS;
   Log(error);
   ShowStatus("retrying - " + error);
}

// The owner's poll interval, bounded below by MIN_POLL_MS.
int PollFloor() { return (InpPollMs < MIN_POLL_MS) ? MIN_POLL_MS : InpPollMs; }

//+------------------------------------------------------------------+
//| Transport                                                         |
//+------------------------------------------------------------------+
// One POST with the key header. Returns the HTTP status, or -1 with the
// terminal's error explained in g_last_error (4014 is the URL not being
// allowed -- by far the commonest install mistake, so the message says
// exactly which dialog to open).
int Post(const string path, const string body, string &response)
{
   string url = g_server + path;
   string headers = KEY_HEADER + ": " + InpKey + "\r\nContent-Type: application/json\r\n";
   char data[];
   char result[];
   string result_headers = "";
   int n = StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0) ArrayResize(data, n - 1);          // drop the terminating NUL
   ResetLastError();
   int code = WebRequest("POST", url, headers, HTTP_TIMEOUT_MS, data, result, result_headers);
   if(code == -1)
   {
      int err = GetLastError();
      if(err == 4014)
         g_last_error = "WebRequest is not allowed for " + g_server +
                        ": open Tools > Options > Expert Advisors, tick 'Allow WebRequest for listed URL' and add " +
                        g_server;
      else
         g_last_error = "WebRequest failed, error " + IntegerToString(err);
      response = "";
      return -1;
   }
   response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return code;
}

//+------------------------------------------------------------------+
//| Event handlers                                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   g_server = InpServer;
   while(StringLen(g_server) > 0 && StringSubstr(g_server, StringLen(g_server) - 1) == "/")
      g_server = StringSubstr(g_server, 0, StringLen(g_server) - 1);
   if(StringFind(InpKey, KEY_PREFIX) != 0 || StringLen(InpKey) < 10)
   {
      Log("InpKey must be the key shown when the account was added (it starts with mt5_)");
      ShowStatus("error: InpKey is not a MirrorFleet key");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(StringFind(g_server, "https://") != 0 && StringFind(g_server, "http://") != 0)
   {
      Log("InpServer must start with https://");
      ShowStatus("error: InpServer must start with https://");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_login   = AccountInfoInteger(ACCOUNT_LOGIN);
   g_hedging = (AccountInfoInteger(ACCOUNT_MARGIN_MODE) == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING);
   g_poll_ms = PollFloor();
   g_state   = ST_HELLO;
   g_hello_chunk = 1;
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   g_trade.SetAsyncMode(false);
   g_trade.LogLevel(LOG_LEVEL_ERRORS);
   LoadAcks();
   Log(g_hedging ? "hedging account" : "netting account: one net position per symbol, an open may reduce, close or flip it");
   if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0 || MQLInfoInteger(MQL_TRADE_ALLOWED) == 0)
      Log("AutoTrading is off: reports will flow, but no command can execute until it is on");
   ShowStatus("connecting");
   // No WebRequest from OnInit: the first tick says hello, and every
   // network failure is then handled in one place (OnTimer).
   EventSetMillisecondTimer(MIN_POLL_MS);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}

void OnTimer()
{
   if(g_state == ST_STOPPED)
   {
      EventKillTimer();
      return;
   }
   g_dirty = false;
   if(g_state == ST_HELLO)
   {
      if(!SendHello())
      {
         EventSetMillisecondTimer(RETRY_POLL_MS);
         return;
      }
      if(g_state != ST_RUNNING)            // another chunk to send, or stopped
      {
         EventSetMillisecondTimer(MIN_POLL_MS);
         return;
      }
   }
   Sync();
   EventSetMillisecondTimer(g_poll_ms);
}

// The book changed: the next tick is now, so acks and deals leave at once.
// Transactions queue behind a running OnTimer (one thread), so this never
// interrupts a request in flight.
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(g_state != ST_RUNNING) return;
   switch(trans.type)
   {
      case TRADE_TRANSACTION_DEAL_ADD:
      case TRADE_TRANSACTION_ORDER_ADD:
      case TRADE_TRANSACTION_ORDER_UPDATE:
      case TRADE_TRANSACTION_ORDER_DELETE:
      case TRADE_TRANSACTION_HISTORY_ADD:
      case TRADE_TRANSACTION_POSITION:
         if(!g_dirty)
         {
            g_dirty = true;
            EventSetMillisecondTimer(10);
         }
         break;
      default:
         break;
   }
}
