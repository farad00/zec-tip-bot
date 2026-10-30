import {createHash,randomBytes} from 'node:crypto';
import type {Config} from './config.js';

// X requires both read scopes for GET /2/users/me. Never inherit the bot's grant.
export const USER_SIGNIN_SCOPES = ['users.read','tweet.read'] as const;
export class UserAuthError extends Error {
  constructor(readonly status?:number) {super('X sign-in failed.');}
}
function client(c:Config) {
  if(!c.userClientId||!c.userClientSecret||/[\s:\x00-\x1f\x7f]/.test(c.userClientId)||/[\s\x00-\x1f\x7f]/.test(c.userClientSecret))throw new UserAuthError();
  return 'Basic '+Buffer.from(encodeURIComponent(c.userClientId)+':'+encodeURIComponent(c.userClientSecret)).toString('base64');
}
export function beginUserSignin(c:Config) {
  client(c);
  const state=randomBytes(32).toString('base64url'),verifier=randomBytes(48).toString('base64url');
  const url=new URL('https://x.com/i/oauth2/authorize');
  url.search=new URLSearchParams({response_type:'code',client_id:c.userClientId,redirect_uri:c.userCallback,
    scope:USER_SIGNIN_SCOPES.join(' '),state,code_challenge:createHash('sha256').update(verifier).digest('base64url'),code_challenge_method:'S256'}).toString();
  return {state,verifier,url:url.href};
}
async function jsonResponse(response:Response):Promise<unknown> {
  if(!response.ok)throw new UserAuthError(response.status);
  if(!response.body)throw new UserAuthError();
  const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
  try {for(;;){const next=await reader.read();if(next.done)break;size+=next.value.length;if(size>16384)throw new UserAuthError();chunks.push(next.value);}}
  finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  try{return JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw new UserAuthError();}
}
async function revoke(c:Config,token:string,fetcher:typeof fetch) {
  // Best effort: a failed revocation never persists a token or grants offline access.
  try {const response=await fetcher('https://api.x.com/2/oauth2/revoke',{method:'POST',headers:{Authorization:client(c),'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({token,client_id:c.userClientId}),redirect:'error',signal:AbortSignal.timeout(5000)});await response.body?.cancel();}catch{/* No upstream text or tokens leave this function. */}
}
export async function finishUserSignin(c:Config,code:string,verifier:string,reserveRead:()=>void,fetcher:typeof fetch=fetch) {
  let token:string|undefined;
  try {
    const response=await fetcher('https://api.x.com/2/oauth2/token',{method:'POST',headers:{Authorization:client(c),'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({grant_type:'authorization_code',code,redirect_uri:c.userCallback,code_verifier:verifier}),redirect:'error',signal:AbortSignal.timeout(15000)});
    const data=await jsonResponse(response) as Record<string,unknown>;
    if(!data||typeof data.access_token!=='string'||!/^[-A-Za-z0-9._~+/]+=*$/.test(data.access_token)||data.access_token.length>8192)throw new UserAuthError();
    token=data.access_token;
    const scopes=typeof data.scope==='string'?data.scope.split(' '):[];
    if(typeof data.token_type!=='string'||data.token_type.toLowerCase()!=='bearer'||data.refresh_token!==undefined||
      scopes.length!==USER_SIGNIN_SCOPES.length||new Set(scopes).size!==scopes.length||USER_SIGNIN_SCOPES.some(scope=>!scopes.includes(scope))||
      typeof data.expires_in!=='number'||!Number.isInteger(data.expires_in)||data.expires_in<1||data.expires_in>7200)throw new UserAuthError();
    reserveRead();
    const me=await fetcher('https://api.x.com/2/users/me',{headers:{Authorization:'Bearer '+token},redirect:'error',signal:AbortSignal.timeout(15000)});
    const identity=await jsonResponse(me) as {data?:{id?:unknown;username?:unknown}};
    if(!identity||typeof identity.data?.id!=='string'||!/^\d{1,25}$/.test(identity.data.id)||typeof identity.data.username!=='string'||!/^\w{1,15}$/.test(identity.data.username))throw new UserAuthError();
    return {id:identity.data.id,username:identity.data.username};
  }catch(error){throw error instanceof UserAuthError?error:new UserAuthError();}
  finally{if(token)await revoke(c,token,fetcher);}
}
