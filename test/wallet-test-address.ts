// Synthetic address-container fixture only. Never send funds to these bytes.
import {bech32m} from '@scure/base';
import {blake2b} from '@noble/hashes/blake2.js';
export function testUnifiedAddress(n:number){
 const padding=Buffer.alloc(16);padding.write('utest');
 const raw=Buffer.concat([Buffer.from([3,43]),Buffer.alloc(43,n),padding]);
 const left=Math.min(64,Math.floor(raw.length/2)),a=raw.subarray(0,left),b=raw.subarray(left);
 const xor=(to:Uint8Array,from:Uint8Array,offset=0)=>{for(let i=0;i<from.length&&i+offset<to.length;i++)to[i+offset]=to[i+offset]!^from[i]!;};
 const h=(i:number)=>xor(a,blake2b(b,{dkLen:a.length,personalization:Buffer.concat([Buffer.from('UA_F4Jumble_H'),Buffer.from([i,0,0])])}));
 const g=(i:number)=>{for(let j=0;j<Math.ceil(b.length/64);j++){const block=Buffer.alloc(3);block[0]=i;block.writeUInt16LE(j,1);xor(b,blake2b(a,{dkLen:64,personalization:Buffer.concat([Buffer.from('UA_F4Jumble_G'),block])}),j*64);}};
 g(0);h(0);g(1);h(1);
 return bech32m.encode('utest',bech32m.toWords(raw),2000);
}
