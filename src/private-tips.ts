import type { Config } from './config.js';
import type { State } from './state.js';
import { TipStore } from './tip-store.js';
import { XError, type Mention, type XApi } from './x.js';
export type PrivateTipPlan = { text?:string; apply?:()=>void; privateAction?:boolean };
function commandText(m:Mention,botId:string):string|undefined{
 const text=m.text.trim();
 if(/^@zectipbot\s/i.test(text))return text;
 // X hides reply-context mentions outside display_text_range. Never strip a
 // user-written visible prefix or suffix, and bind every hidden tag to X's IDs.
 const range=m.display_text_range;
 if(!Array.isArray(range)||range.length!==2||!range.every(Number.isInteger))return;
 const [start,end]=range;
 if(start<=0||start>=end||end!==m.text.length)return;
 const parents=m.referenced_tweets?.filter(parent=>parent.type==='replied_to')??[];
 if(parents.length!==1)return;
 const bot=m.entities?.mentions?.find(entity=>entity.username.toLowerCase()==='zectipbot'&&entity.id===botId&&entity.start===start);
 if(!bot)return;
 const prefix=m.text.slice(0,start),tags=[...prefix.matchAll(/@([A-Za-z0-9_]{1,15})\s+/g)];
 if(!tags.length||tags.length>10||prefix.replace(/@[A-Za-z0-9_]{1,15}\s+/g,'').trim()||new Set(tags.map(tag=>tag[1]!.toLowerCase())).size!==tags.length)return;
 if(tags.some(tag=>!m.entities?.mentions?.some(entity=>entity.start===tag.index&&entity.username.toLowerCase()===tag[1]!.toLowerCase()&&!!entity.id&&/^\d{1,20}$/.test(entity.id))))return;
 const visible=m.text.slice(start);
 if(!/^@zectipbot\s+(?:tip(?:\s+@[A-Za-z0-9_]{1,15})?|help|stop|start)\s*$/i.test(visible))return;
 return visible.trim();
}
export async function privateMention(m:Mention,c:Config,state:State,api:Pick<XApi,'lookup'> & Partial<Pick<XApi,'postAuthor'>>):Promise<PrivateTipPlan|undefined> {
  const store=new TipStore(state), profile=store.profile(m.author_id,c.network);
  if(m.text.length>2400)return;
  const normalized=commandText(m,c.botId);if(!normalized)return;
  const body=/^@zectipbot\s+(.+)$/i.exec(normalized)?.[1];if(!body)return;
  if(/^stop$/i.test(body))return {privateAction:true,apply:()=>{store.disable(m.author_id,c.network);state.opt(m.author_id,true);}};
  if(/^start$/i.test(body))return {text:'Replies resumed. Automatic spending stays paused until you enable it in your private account settings.',apply:()=>state.opt(m.author_id,false)};
  if(state.user(m.author_id)?.opted_out)return;
  if(/^help$/i.test(body))return {text:`Set your personal default privately: ${c.baseUrl}/account Then comment @zectipbot tip under a post, or @zectipbot tip @recipient. Testnet preview; check your account for availability. Public comments reveal tipping intent.`};
  if(/^(register|request|unregister)\b/i.test(body))return {text:'Public address registration and payment links are disabled in private tipping mode. Never post addresses, seeds or keys. Use your private account settings.'};
  const tip=/^tip(?:\s+@([A-Za-z0-9_]{1,15}))?$/i.exec(body);
  if(!tip){if(/^tip\b/i.test(body))return {text:'Use @zectipbot tip under a post, or @zectipbot tip @recipient. Your amount comes from private settings. Do not include an amount or address publicly.'};return;}
  if(Array.isArray(m.edit_history_tweet_ids)&&m.edit_history_tweet_ids.length>1)return {text:'Edited tip commands are not processed. Check your private activity before posting a new command.'};
  const acknowledgement=profile?.public_ack?`Command received. Check your private account for status: ${c.baseUrl}/account`:undefined;
  let recipient:string;
  try {
  if(tip[1])recipient=(await api.lookup(tip[1].toLowerCase())).id;
  else {
    const parents=m.referenced_tweets?.filter(p=>p.type==='replied_to')??[];
    if(parents.length!==1 || !api.postAuthor)return {text:'Reply directly to the post you want to tip with @zectipbot tip, or name the recipient: @zectipbot tip @recipient.'};
    recipient=(await api.postAuthor(parents[0]!.id)).id;
  }
  } catch(error) {
    if(error instanceof XError && error.status===404) {
      const commandTime=Number((BigInt(m.id)>>22n)+1288834974657n);
      return {text:acknowledgement,privateAction:true,apply:()=>{store.enqueue(m.id,m.author_id,'0',c.network,commandTime);}};
    }
    throw error;
  }
  if(recipient===c.botId)return {text:'Choose a recipient other than the bot. No tip was queued.'};
  // Use X's immutable creation timestamp (snowflake), never receipt time. Reject old/replayed authorizations.
  const commandTime=Number((BigInt(m.id)>>22n)+1288834974657n);
  return {text:acknowledgement,privateAction:true,apply:()=>{store.enqueue(m.id,m.author_id,recipient,c.network,commandTime);}};
}


