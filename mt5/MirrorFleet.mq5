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

//+------------------------------------------------------------------+
//| Hello: who this terminal is and what it can trade                 |
//+------------------------------------------------------------------+
// Every symbol the terminal can trade, fixed for one hello round so the
// chunks agree on `chunks`.
void CollectSymbols()
{
   ArrayResize(g_hello_symbols, 0);
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
   {
      string name = SymbolName(i, false);
      if(SymbolInfoInteger(name, SYMBOL_TRADE_MODE) == SYMBOL_TRADE_MODE_DISABLED) continue;
      int n = ArraySize(g_hello_symbols);
      ArrayResize(g_hello_symbols, n + 1, 256);
      g_hello_symbols[n] = name;
   }
   int count = ArraySize(g_hello_symbols);
   g_hello_chunks = (count + SYMBOLS_PER_HELLO - 1) / SYMBOLS_PER_HELLO;
   if(g_hello_chunks < 1) g_hello_chunks = 1;     // an empty list is still one hello
}

// The hello body for one chunk (the spec's example, key for key).
string HelloBody(const int chunk)
{
   string trade_mode = "real";
   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_DEMO)         trade_mode = "demo";
   else if(mode == ACCOUNT_TRADE_MODE_CONTEST) trade_mode = "contest";

   string symbols = "";
   int first = (chunk - 1) * SYMBOLS_PER_HELLO;
   int last = first + SYMBOLS_PER_HELLO;
   if(last > ArraySize(g_hello_symbols)) last = ArraySize(g_hello_symbols);
   for(int i = first; i < last; i++)
   {
      string s = g_hello_symbols[i];
      if(symbols != "") symbols += ",";
      symbols += "{" + JField("n", JStr(s)) + "," +
                 JField("d", IntegerToString(SymbolInfoInteger(s, SYMBOL_DIGITS))) + "," +
                 JField("cs", JTrim(SymbolInfoDouble(s, SYMBOL_TRADE_CONTRACT_SIZE), 4)) + "," +
                 JField("vmin", JLots(SymbolInfoDouble(s, SYMBOL_VOLUME_MIN))) + "," +
                 JField("vstep", JLots(SymbolInfoDouble(s, SYMBOL_VOLUME_STEP))) + "," +
                 JField("vmax", JLots(SymbolInfoDouble(s, SYMBOL_VOLUME_MAX))) + "," +
                 JField("tm", IntegerToString(SymbolInfoInteger(s, SYMBOL_TRADE_MODE))) + "}";
   }
   return "{" + JField("v", IntegerToString(PROTOCOL_VERSION)) + "," +
          JField("ea", JStr(EA_VERSION)) + "," +
          JField("build", IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))) + "," +
          JField("login", IntegerToString(g_login)) + "," +
          JField("broker", JStr(AccountInfoString(ACCOUNT_COMPANY))) + "," +
          JField("server", JStr(AccountInfoString(ACCOUNT_SERVER))) + "," +
          JField("currency", JStr(AccountInfoString(ACCOUNT_CURRENCY))) + "," +
          JField("hedging", JBool(g_hedging)) + "," +
          JField("trade_mode", JStr(trade_mode)) + "," +
          JField("leverage", IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE))) + "," +
          JField("symbols", "[" + symbols + "]") + "," +
          JField("chunk", IntegerToString(chunk)) + "," +
          JField("chunks", IntegerToString(g_hello_chunks)) + "}";
}

// One chunk of the hello. The reply to the last chunk is JSON,
// {"last_deal_ticket": N} (contract section 2: mt5_hello returns a dict and
// the api passes it through) -- NOT an OK line -- and N is where this
// terminal's deal reporting resumes, so a restarted EA neither repeats nor
// skips a deal.
bool SendHello()
{
   if(g_hello_chunk <= 1) CollectSymbols();
   string reply = "";
   int code = Post(HELLO_PATH, HelloBody(g_hello_chunk), reply);
   if(code == 401)
   {
      Stop(JsonString(reply, "reason", "unknown key"));
      return true;
   }
   if(code != 200)
   {
      // -1 left its explanation in g_last_error; 503 is the copier being
      // away (its body carries a reason); anything else is shown as is.
      if(code != -1)
         g_last_error = "hello answered HTTP " + IntegerToString(code) + " " +
                        JsonString(reply, "reason", "");
      Retrying(g_last_error);
      g_hello_chunk = 1;                    // a round restarts from the chunk that resets the list
      return false;
   }
   if(g_hello_chunk < g_hello_chunks)
   {
      ShowStatus(StringFormat("connecting (hello %d/%d)", g_hello_chunk, g_hello_chunks));
      g_hello_chunk++;
      return true;
   }
   g_watermark = (ulong)JsonLong(reply, "last_deal_ticket", 0);
   g_watermark_time = 0;                    // unknown until the first sync finds the ticket
   g_hello_chunk = 1;
   // The hello carried the margin mode; hedging and netting both run.
   g_state = ST_RUNNING;
   g_seq = 0;
   g_poll_ms = PollFloor();
   Log(StringFormat("connected as login %I64d with %d symbols; deals resume after ticket %I64u",
                    g_login, ArraySize(g_hello_symbols), g_watermark));
   ShowStatus("connected");
   return true;
}

//+------------------------------------------------------------------+
//| Sync: the report                                                  |
//+------------------------------------------------------------------+
// MT5 keeps every datetime in the broker's clock. The wire carries UTC, so
// the offset is estimated once per sync (brokers sit on whole or half
// hours; rounding hides the request latency and a clock a few seconds out).
void UpdateServerOffset()
{
   long diff = (long)TimeTradeServer() - (long)TimeGMT();
   g_server_offset_s = (long)MathRound((double)diff / 1800.0) * 1800;
}

// The four pending kinds the copier knows; "" for anything else (stop-limit
// orders and market orders in flight are not reported).
string PendingTypeName(const long type)
{
   if(type == ORDER_TYPE_BUY_LIMIT)  return "BUY_LIMIT";
   if(type == ORDER_TYPE_SELL_LIMIT) return "SELL_LIMIT";
   if(type == ORDER_TYPE_BUY_STOP)   return "BUY_STOP";
   if(type == ORDER_TYPE_SELL_STOP)  return "SELL_STOP";
   return "";
}

string PositionsJson()
{
   string out = "";
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);          // selects the position as well
      if(ticket == 0) continue;
      string s = PositionGetString(POSITION_SYMBOL);
      int digits = (int)SymbolInfoInteger(s, SYMBOL_DIGITS);
      bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      if(out != "") out += ",";
      out += "{" + JField("t", (string)ticket) + "," +
             JField("s", JStr(s)) + "," +
             JField("side", JStr(buy ? "BUY" : "SELL")) + "," +
             JField("lots", JLots(PositionGetDouble(POSITION_VOLUME))) + "," +
             JField("open", JNum(PositionGetDouble(POSITION_PRICE_OPEN), digits)) + "," +
             JField("sl", JNum(PositionGetDouble(POSITION_SL), digits)) + "," +
             JField("tp", JNum(PositionGetDouble(POSITION_TP), digits)) + "," +
             JField("price", JNum(PositionGetDouble(POSITION_PRICE_CURRENT), digits)) + "," +
             JField("pnl", JNum(PositionGetDouble(POSITION_PROFIT), 2)) + "," +
             JField("swap", JNum(PositionGetDouble(POSITION_SWAP), 2)) + "," +
             JField("comment", JStr(PositionGetString(POSITION_COMMENT))) + "," +
             JField("magic", IntegerToString(PositionGetInteger(POSITION_MAGIC))) + "," +
             JField("time", IntegerToString(PositionGetInteger(POSITION_TIME) - g_server_offset_s)) + "}";
   }
   return "[" + out + "]";
}

string OrdersJson()
{
   string out = "";
   int total = OrdersTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = OrderGetTicket(i);             // selects the order as well
      if(ticket == 0) continue;
      string type_name = PendingTypeName(OrderGetInteger(ORDER_TYPE));
      if(type_name == "") continue;
      string s = OrderGetString(ORDER_SYMBOL);
      int digits = (int)SymbolInfoInteger(s, SYMBOL_DIGITS);
      if(out != "") out += ",";
      out += "{" + JField("t", (string)ticket) + "," +
             JField("s", JStr(s)) + "," +
             JField("type", JStr(type_name)) + "," +
             JField("lots", JLots(OrderGetDouble(ORDER_VOLUME_CURRENT))) + "," +
             JField("price", JNum(OrderGetDouble(ORDER_PRICE_OPEN), digits)) + "," +
             JField("sl", JNum(OrderGetDouble(ORDER_SL), digits)) + "," +
             JField("tp", JNum(OrderGetDouble(ORDER_TP), digits)) + "," +
             JField("comment", JStr(OrderGetString(ORDER_COMMENT))) + "," +
             JField("magic", IntegerToString(OrderGetInteger(ORDER_MAGIC))) + "}";
   }
   return "[" + out + "]";
}

// One deal, already selected with HistoryDealSelect.
string DealJson(const ulong t)
{
   string s = HistoryDealGetString(t, DEAL_SYMBOL);
   int digits = (s == "") ? 2 : (int)SymbolInfoInteger(s, SYMBOL_DIGITS);
   long type = HistoryDealGetInteger(t, DEAL_TYPE);
   string type_name = "OTHER";
   if(type == DEAL_TYPE_BUY)          type_name = "BUY";
   else if(type == DEAL_TYPE_SELL)    type_name = "SELL";
   else if(type == DEAL_TYPE_BALANCE) type_name = "BALANCE";
   else if(type == DEAL_TYPE_CREDIT)  type_name = "CREDIT";
   string entry_name = "";
   if(type == DEAL_TYPE_BUY || type == DEAL_TYPE_SELL)
   {
      long entry = HistoryDealGetInteger(t, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN)          entry_name = "IN";
      else if(entry == DEAL_ENTRY_OUT)    entry_name = "OUT";
      else if(entry == DEAL_ENTRY_INOUT)  entry_name = "INOUT";
      else if(entry == DEAL_ENTRY_OUT_BY) entry_name = "OUT_BY";
   }
   return "{" + JField("t", (string)t) + "," +
          JField("pos", IntegerToString(HistoryDealGetInteger(t, DEAL_POSITION_ID))) + "," +
          JField("order", IntegerToString(HistoryDealGetInteger(t, DEAL_ORDER))) + "," +
          JField("s", JStr(s)) + "," +
          JField("type", JStr(type_name)) + "," +
          JField("entry", JStr(entry_name)) + "," +
          JField("lots", JLots(HistoryDealGetDouble(t, DEAL_VOLUME))) + "," +
          JField("price", JNum(HistoryDealGetDouble(t, DEAL_PRICE), digits)) + "," +
          JField("profit", JNum(HistoryDealGetDouble(t, DEAL_PROFIT), 2)) + "," +
          JField("swap", JNum(HistoryDealGetDouble(t, DEAL_SWAP), 2)) + "," +
          JField("commission", JNum(HistoryDealGetDouble(t, DEAL_COMMISSION), 2)) + "," +
          JField("time", IntegerToString(HistoryDealGetInteger(t, DEAL_TIME_MSC) - g_server_offset_s * 1000)) + "," +
          JField("comment", JStr(HistoryDealGetString(t, DEAL_COMMENT))) + "," +
          JField("magic", IntegerToString(HistoryDealGetInteger(t, DEAL_MAGIC))) + "}";
}

// Every deal past the watermark, ascending by ticket, at most
// MAX_DEALS_PER_SYNC and never past the body cap; the rest follow on the
// next polls. Reports the last ticket and its server time so Sync can
// advance the watermark once the server says OK.
string DealsJson(const int body_so_far, ulong &max_ticket, datetime &max_time)
{
   max_ticket = 0;
   max_time = 0;
   datetime from = (g_watermark_time == 0) ? 0 : g_watermark_time - DEAL_LOOKBACK_S;
   if(!HistorySelect(from, TimeCurrent() + 86400)) return "[]";
   ulong fresh[];
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if(t == g_watermark && g_watermark_time == 0)
         g_watermark_time = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
      if(t <= g_watermark) continue;
      int n = ArraySize(fresh);
      ArrayResize(fresh, n + 1, 256);
      fresh[n] = t;
   }
   int count = ArraySize(fresh);
   if(count == 0)
   {
      // Nothing newer than the watermark anywhere: later syncs need only
      // the one-hour window.
      if(g_watermark_time == 0) g_watermark_time = TimeCurrent();
      return "[]";
   }
   ArraySort(fresh);
   string out = "";
   int size = body_so_far;
   for(int i = 0; i < count && i < MAX_DEALS_PER_SYNC; i++)
   {
      ulong t = fresh[i];
      if(!HistoryDealSelect(t)) continue;
      string item = DealJson(t);
      if(size + StringLen(item) > MAX_SYNC_BODY_BYTES) break;
      if(out != "") out += ",";
      out += item;
      size += StringLen(item) + 1;
      max_ticket = t;
      max_time = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
   }
   return "[" + out + "]";
}

string AcksJson()
{
   string out = "";
   int n = ArraySize(g_pending_acks);
   for(int i = 0; i < n; i++)
   {
      if(out != "") out += ",";
      out += g_pending_acks[i];
   }
   return "[" + out + "]";
}

void PushAck(const string ack)
{
   int n = ArraySize(g_pending_acks);
   ArrayResize(g_pending_acks, n + 1);
   g_pending_acks[n] = ack;
}

// The first `count` acks were delivered: drop them, keep the ones the
// commands of this tick appended after the POST.
void DropSentAcks(const int count)
{
   int n = ArraySize(g_pending_acks);
   if(count <= 0) return;
   if(count >= n)
   {
      ArrayResize(g_pending_acks, 0);
      return;
   }
   for(int i = count; i < n; i++) g_pending_acks[i - count] = g_pending_acks[i];
   ArrayResize(g_pending_acks, n - count);
}

// The sync body (the spec's example, key for key). Deals go last so the
// size guard sees everything else first.
string SyncBody(ulong &max_ticket, datetime &max_time)
{
   g_seq++;
   string body = "{" + JField("v", IntegerToString(PROTOCOL_VERSION)) + "," +
                 JField("seq", IntegerToString(g_seq)) + "," +
                 JField("ts", IntegerToString((long)TimeGMT() * 1000)) + "," +
                 JField("balance", JNum(AccountInfoDouble(ACCOUNT_BALANCE), 2)) + "," +
                 JField("equity", JNum(AccountInfoDouble(ACCOUNT_EQUITY), 2)) + "," +
                 JField("margin", JNum(AccountInfoDouble(ACCOUNT_MARGIN), 2)) + "," +
                 JField("margin_free", JNum(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2)) + "," +
                 JField("positions", PositionsJson()) + "," +
                 JField("orders", OrdersJson()) + "," +
                 JField("acks", AcksJson()) + ",";
   body += JField("deals", DealsJson(StringLen(body), max_ticket, max_time)) + "}";
   return body;
}

//+------------------------------------------------------------------+
//| Sync: the reply                                                   |
//+------------------------------------------------------------------+
// The server's next_poll_ms is a floor it may raise, never a way past the
// owner's InpPollMs; an unreadable value falls back.
int PollFrom(const string text, const int fallback)
{
   int ms = (int)StringToInteger(text);
   if(ms <= 0) ms = fallback;
   int floor_ms = PollFloor();
   return (ms < floor_ms) ? floor_ms : ms;
}

// One poll: the report out, the status line and the commands in.
void Sync()
{
   UpdateServerOffset();
   ulong sent_max_ticket = 0;
   datetime sent_max_time = 0;
   int sent_acks = ArraySize(g_pending_acks);
   string body = SyncBody(sent_max_ticket, sent_max_time);
   string reply = "";
   int code = Post(SYNC_PATH, body, reply);
   if(code == 401)
   {
      Stop(JsonString(reply, "reason", "unknown key"));
      return;
   }
   if(code != 200)
   {
      if(code != -1)
         g_last_error = "sync answered HTTP " + IntegerToString(code) + " " +
                        JsonString(reply, "reason", "");
      Retrying(g_last_error);
      return;
   }

   // Lines: a status line, then one CMD line per command (contract section 2).
   StringReplace(reply, "\r", "");
   string lines[];
   int count = StringSplit(reply, '\n', lines);
   if(count < 1 || lines[0] == "")
   {
      Retrying("empty reply from the server");
      return;
   }
   string fields[];
   int nf = StringSplit(lines[0], '\t', fields);
   string status = (nf > 0) ? fields[0] : "";

   if(status == "STOP")
   {
      // STOP <server_ms> <reason>: three fields, the reason is the third.
      string reason = "stopped by the server";
      if(nf >= 3)
      {
         reason = fields[2];
         for(int i = 3; i < nf; i++) reason += " " + fields[i];
      }
      Stop(reason);
      return;
   }
   if(status == "RETRY")
   {
      // RETRY <server_ms> <next_poll_ms>: nothing was applied. The acks
      // and the deals stay for the next poll.
      g_last_error = "the copier is unavailable";
      g_poll_ms = (nf >= 3) ? PollFrom(fields[2], RETRY_POLL_MS) : RETRY_POLL_MS;
      ShowStatus("retrying - " + g_last_error);
      return;
   }
   if(status != "OK")
   {
      Retrying("unexpected status line '" + lines[0] + "'");
      return;
   }

   // OK <server_ms> <next_poll_ms>: the report was applied.
   g_poll_ms = (nf >= 3) ? PollFrom(fields[2], PollFloor()) : PollFloor();
   DropSentAcks(sent_acks);
   if(sent_max_ticket > g_watermark)
   {
      g_watermark = sent_max_ticket;
      g_watermark_time = sent_max_time;
   }
   g_last_error = "";
   g_last_sync = TimeLocal();
   ShowStatus("connected - last sync " + TimeToString(g_last_sync, TIME_SECONDS) + " - v" + EA_VERSION);
   for(int i = 1; i < count; i++)
   {
      if(lines[i] == "") continue;
      ExecuteLine(lines[i]);
   }
}
