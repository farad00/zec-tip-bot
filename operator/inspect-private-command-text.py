#!/usr/bin/env python3
"""Add structural text diagnostics to the installed no-post/no-payment diagnostic."""
import importlib.util
import os
from pathlib import Path
import stat
import sys
EXTRA='''if(post){
const entity=post.entities?.mentions?.find(m=>m.username?.toLowerCase()==="zectipbot"),start=Number.isInteger(entity?.start)?entity.start:post.text.toLowerCase().indexOf("@zectipbot"),prefix=start>=0?post.text.slice(0,start):"";
report.botEntityIdPresent=typeof entity?.id==="string";report.botEntityIdMatches=entity?.id===c.botId;
report.botEntityOffset=start;report.prefixKind=prefix.trim()===""?"blank":/^(?:@[A-Za-z0-9_]{1,15}\\s+)+$/.test(prefix)?"reply_mentions":"other_text";
report.botAndSuffixMatch=start>=0&&/^@zectipbot\\s+tip(?:\\s+@[A-Za-z0-9_]{1,15})?$/i.test(post.text.slice(start).trim());
report.leadingMentionIdsPresent=post.entities?.mentions?.filter(m=>Number.isInteger(m.start)&&m.start<start).every(m=>typeof m.id==="string")??false;
report.invisibleCodePoints=[...new Set(Array.from(post.text).filter(ch=>/[\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u206f\\ufeff]/.test(ch)).map(ch=>"U+"+ch.codePointAt(0).toString(16).toUpperCase()))];
report.displayTextRange=post.display_text_range??null;
if(Array.isArray(post.display_text_range)&&post.display_text_range.length===2){const [a,b]=post.display_text_range;report.displaySliceMatches=/^@zectipbot\\s+tip(?:\\s+@[A-Za-z0-9_]{1,15})?$/i.test(post.text.slice(a,b).trim());}
report.exactSupportedTip='''
def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    path=Path('/root/diagnose-private-tip.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the installed protected diagnostic.')
    spec=importlib.util.spec_from_file_location('inspect_command_base',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    old='if(post){report.exactSupportedTip='
    if module.CODE.count(old)!=1:raise ValueError('The original diagnostic differs from the expected version.')
    module.CODE=module.CODE.replace(old,EXTRA).replace('tweet.fields=author_id,created_at,entities,referenced_tweets','tweet.fields=author_id,created_at,entities,referenced_tweets,display_text_range')
    module.CODE=module.CODE.replace('const recipient=await api.postAuthor(parents[0].id);',
        'const recipient=await api.postAuthor(parents[0].id);const leading=post.entities?.mentions?.filter(m=>Number.isInteger(m.start)&&m.start<start)??[];report.leadingMentionsMatchReplyContext=leading.length>0&&leading.every(m=>m.id===post.author_id||m.id===recipient.id);')
    module.main()
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Command structure check stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
