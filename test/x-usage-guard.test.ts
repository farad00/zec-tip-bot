import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadConfig} from '../src/config.js';
import {State,BudgetLimitError,DailyReplyLimitError} from '../src/state.js';

test('X testing guard stays on by default and disabling it keeps accounting and per-run limits',()=>{
 const guarded=loadConfig({DAILY_X_BUDGET_USD:'0.001',MAX_REPLIES_PER_DAY:'1'}),open=loadConfig({X_USAGE_GUARD_ENABLED:'false',DAILY_X_BUDGET_USD:'0.001',MAX_REPLIES_PER_DAY:'1'});
 assert.equal(guarded.usageGuard,true);assert.equal(open.usageGuard,false);
 assert.equal(open.maxCalls,guarded.maxCalls);assert.equal(open.pollSeconds,guarded.pollSeconds);assert.equal(open.maxReplies,guarded.maxReplies);
 assert.throws(()=>loadConfig({X_USAGE_GUARD_ENABLED:'maybe'}));
 const closed=new State(':memory:'),unrestricted=new State(':memory:');
 try{
  assert.throws(()=>closed.reserveCost(2,guarded.budgetMills),BudgetLimitError);
  closed.reserveReply('1',guarded.maxDailyReplies);assert.throws(()=>closed.reserveReply('2',guarded.maxDailyReplies),DailyReplyLimitError);
  unrestricted.reserveCost(2,open.budgetMills);unrestricted.reserveReply('1',open.maxDailyReplies);unrestricted.reserveReply('2',open.maxDailyReplies);
  const settle=unrestricted.reserveRequest(100,open.budgetMills);settle(3);
  const usage=unrestricted.db.prepare('SELECT mills,replies FROM usage').get() as {mills:number;replies:number};
  assert.equal(usage.mills,5);assert.equal(usage.replies,2);
 }finally{closed.close();unrestricted.close();}
});
