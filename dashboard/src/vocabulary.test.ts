import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from 'vitest'

// The product speaks one language. These scans read the shipped source, not
// the DOM, so a stray string in a rarely rendered branch cannot hide.
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

const files = [...sources('src/pages'), ...sources('src/components')]

// Comments are for maintainers, not users: scan the code with them removed
// (block and JSX comments, and line comments; a line comment needs
// whitespace or a line start before it so a URL's "//" inside a string
// survives).
function code(f: string): string {
  return readFileSync(f, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|\s)\/\/.*$/gm, '$1')
}

test('no user-visible "Slave": JSX text, string literals and labels say follower', () => {
  const offenders: string[] = []
  for (const f of files) {
    const src = code(f)
    // A quoted or JSX-text "Slave"/"slave" (identifiers like role === 'slave'
    // compare against the API value and are allowed only in that shape).
    // JSX text runs from a tag's ">" to the next tag or expression; code
    // punctuation (= ; ( ) { }) ends a run, so a TypeScript generic such as
    // useState<T>(...) cannot open a false "text" run across lines of code.
    for (const m of src.matchAll(/(>[^<>{}();=]*\b[Ss]lave\b[^<>{}();=]*[<{]|['"`][^'"`\n]*\b[Ss]lave accounts?\b[^'"`\n]*['"`]|\bSlave\b(?=[^'"`\n]*<\/))/g)) {
      offenders.push(`${f}: ${m[0].trim().slice(0, 60)}`)
    }
  }
  expect(offenders).toEqual([])
})

// String literals: quotes end at the line, template literals may span lines.
// JSX text with an apostrophe can open a false "literal" that runs to the
// next quote on the same line; at worst that is a false alarm on that line.
const LITERAL = /'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`/g

test('no quoted "Slave": labels, titles, ternaries, templates and label maps say follower', () => {
  const offenders: string[] = []
  for (const f of files) {
    for (const m of code(f).matchAll(LITERAL)) {
      const body = m[0].slice(1, -1)
      // The bare API value ('slave') and identifiers such as
      // 'slave_account_id' stay allowed; a capitalised Slave, or a lowercase
      // slave next to another word, is prose a user reads.
      if (/\bSlaves?\b/.test(body) || /\bslaves?\s+\w|\w\s+slaves?\b/.test(body)) {
        offenders.push(`${f}: ${m[0].slice(0, 60)}`)
      }
    }
  }
  expect(offenders).toEqual([])
})

test('the quoted-Slave scan catches every shape and spares the API value', () => {
  const flagged = (src: string) =>
    [...src.matchAll(LITERAL)].some((m) => {
      const body = m[0].slice(1, -1)
      return /\bSlaves?\b/.test(body) || /\bslaves?\s+\w|\w\s+slaves?\b/.test(body)
    })
  expect(flagged('<td title="Slave copy">')).toBe(true)
  expect(flagged('aria-label="Close slave position"')).toBe(true)
  expect(flagged("{x ? 'Slave' : 'Master'}")).toBe(true)
  expect(flagged('`${n} slave copies`')).toBe(true)
  expect(flagged("{ slave: 'Slave' }")).toBe(true)
  expect(flagged("role === 'slave'")).toBe(false)
  expect(flagged('"slave"')).toBe(false)
  expect(flagged("a.slave_account_id ?? 'slave_account_id'")).toBe(false)
})

test('no literal "Loading..." or "Loading…" text: pages use the Loading primitive', () => {
  const offenders = files.filter((f) => /Loading(\.\.\.|…)/.test(code(f)))
  expect(offenders).toEqual([])
})

test('no page renders its own h1: headings come from PageHeader or AuthCard', () => {
  const allowed = new Set(['src/components/PageHeader.tsx', 'src/components/AuthCard.tsx', 'src/pages/NotFound.tsx', 'src/pages/Landing.tsx'])
  const offenders = files.filter((f) => !allowed.has(f.replace(/\\/g, '/')) && /<h1[\s>]/.test(code(f)))
  expect(offenders).toEqual([])
})
