import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomBytes } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
export class BudgetLimitError extends Error {}
export class DailyReplyLimitError extends Error {}
export type Payment = { id: string; address: string; amount: string; network: 'mainnet' | 'testnet'; recipient: string; expires: number; source: string };
export class State {
  readonly db: DatabaseSync;
  constructor(path = 'data/bot.sqlite') {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, address TEXT, opted_out INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS payments (id TEXT PRIMARY KEY, address TEXT NOT NULL, amount TEXT NOT NULL, network TEXT NOT NULL, recipient TEXT NOT NULL, expires INTEGER NOT NULL, source TEXT UNIQUE NOT NULL);
      CREATE TABLE IF NOT EXISTS attempts (source TEXT PRIMARY KEY, status TEXT NOT NULL, reply_id TEXT, created INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, mills INTEGER NOT NULL DEFAULT 0, replies INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, until_ms INTEGER NOT NULL);`);
  }
  close() { this.db.close(); }
  get(key: string): string | undefined { return (this.db.prepare('SELECT value FROM settings WHERE key=?').get(key) as { value: string } | undefined)?.value; }
  set(key: string, value: string) { this.db.prepare('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key, value); }
  register(id: string, address: string) { this.db.prepare('INSERT INTO users(id,address) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET address=excluded.address').run(id, address); }
  user(id: string): { address: string | null; opted_out: number } | undefined { return this.db.prepare('SELECT address,opted_out FROM users WHERE id=?').get(id) as { address: string | null; opted_out: number } | undefined; }
  opt(id: string, out: boolean) { this.db.prepare('INSERT INTO users(id,opted_out) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET opted_out=excluded.opted_out,address=CASE WHEN excluded.opted_out=1 THEN NULL ELSE users.address END').run(id, out ? 1 : 0); }
  payment(id: string): Payment | undefined { return this.db.prepare('SELECT * FROM payments WHERE id=?').get(id) as Payment | undefined; }
  createPayment(p: Omit<Payment, 'id'>): Payment {
    const old = this.db.prepare('SELECT * FROM payments WHERE source=?').get(p.source) as Payment | undefined;
    if (old) return old;
    const id = randomBytes(16).toString('hex');
    this.db.prepare('INSERT INTO payments VALUES (?,?,?,?,?,?,?)').run(id, p.address, p.amount, p.network, p.recipient, p.expires, p.source);
    return { ...p, id };
  }
  reserveCost(mills: number, ceiling: number, day = new Date().toISOString().slice(0, 10)) {
    this.db.prepare('INSERT OR IGNORE INTO usage(day) VALUES (?)').run(day);
    const result = this.db.prepare('UPDATE usage SET mills=mills+? WHERE day=? AND mills+?<=?').run(mills, day, mills, ceiling);
    if (!result.changes) throw new BudgetLimitError('Local daily X spending guard reached. Check your X console before adjusting DAILY_X_BUDGET_USD.');
  }
  reserveRequest(mills: number, ceiling: number): (actual: number) => void {
    const day = new Date().toISOString().slice(0, 10);
    this.reserveCost(mills, ceiling, day);
    let settled = false;
    return (actual: number) => {
      if (settled || !Number.isInteger(actual) || actual < 0 || actual > mills) return;
      settled = true;
      this.db.prepare('UPDATE usage SET mills=MAX(0,mills-?) WHERE day=?').run(mills - actual, day);
    };
  }
  unregister(id: string) { this.db.prepare('UPDATE users SET address=NULL WHERE id=?').run(id); }
  attempted(source: string) { return Boolean(this.db.prepare('SELECT 1 FROM attempts WHERE source=?').get(source)); }
  reserveReply(source: string, maxDaily: number, day = new Date().toISOString().slice(0, 10)) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      this.db.prepare('INSERT OR IGNORE INTO usage(day) VALUES (?)').run(day);
      const used = this.db.prepare('UPDATE usage SET replies=replies+1 WHERE day=? AND replies<?').run(day, maxDaily);
      if (!used.changes) throw new DailyReplyLimitError('Daily reply limit reached.');
      this.db.prepare("INSERT INTO attempts VALUES (?,'sending',NULL,?)").run(source, Date.now());
      this.db.exec('COMMIT');
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  finishReply(source: string, replyId: string) { this.db.prepare("UPDATE attempts SET status='sent',reply_id=? WHERE source=?").run(replyId, source); }
  cancelUnsentReply(source: string) {
    // Only an in-flight reservation with proof of no POST may be released.
    // Daily attempt counts stay conservative; unknown/sent records are preserved.
    this.db.prepare("DELETE FROM attempts WHERE source=? AND status='sending'").run(source);
  }
  uncertain(source: string) { this.db.prepare("UPDATE attempts SET status='unknown' WHERE source=?").run(source); }
  status() {
    return {
      cursorInitialized: Boolean(this.get('since_id')),
      workerHeld: Boolean(this.get('worker_hold')),
      usage: this.db.prepare('SELECT * FROM usage ORDER BY day DESC LIMIT 3').all(),
      unresolved: this.db.prepare("SELECT source,status,created FROM attempts WHERE status!='sent'").all(),
      registeredUsers: (this.db.prepare('SELECT COUNT(*) AS n FROM users WHERE address IS NOT NULL AND opted_out=0').get() as { n: number }).n
    };
  }
  acquire(name: string) {
    const r = this.db.prepare('INSERT INTO leases VALUES (?,?) ON CONFLICT(name) DO UPDATE SET until_ms=excluded.until_ms WHERE leases.until_ms<?').run(name, Date.now() + 3_600_000, Date.now());
    if (!r.changes) throw new Error('A bot command is already running (or a crashed run holds a lease for up to one hour).');
  }
  release(name: string) { this.db.prepare('DELETE FROM leases WHERE name=?').run(name); }
}
