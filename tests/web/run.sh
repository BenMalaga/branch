#!/usr/bin/env bash
# Executes the real frontend under a DOM/MapLibre stub. The browser pane in an
# agent session often never composites a frame, so MapLibre never finishes
# loading and everything looks broken when it is fine. This runs the actual
# shipped code instead, and has caught real bugs the browser never showed.
set -u
cd "$(dirname "$0")/../.."
python3 -c "
import re;s=open('web/index.html',encoding='utf-8').read()
open('/tmp/next.js','w').write(re.findall(r'<script>(.*?)</script>',s,re.S)[-1])"
node --check /tmp/next.js || exit 1
fail=0
for t in shell lineage replay interrogate table connector notice finder; do
  out=$(node -e "
    require('$PWD/tests/web/stub.js');const fs=require('fs');
    globalThis.pmtiles={Protocol:class{constructor(){this.tile=()=>{}}}};
    globalThis.document.documentElement={dataset:{},style:{setProperty(){},getPropertyValue(){return ''}}};
    (0,eval)('(async()=>{'+fs.readFileSync('/tmp/next.js','utf8').replace(/\ninit\(\);\s*\$/,'')
      +'\n;\n'+fs.readFileSync('$PWD/tests/web/'+'$t'+'.js','utf8')
      +'})().catch(e=>{console.log(\"CRASH \"+(e&&e.message));process.exitCode=1})');
  " 2>&1 | grep -v Warning)
  n=$(echo "$out" | grep -cE '^PASS|OK$'); f=$(echo "$out" | grep -cE '^FAIL')
  c=$(echo "$out" | grep -cE '^CRASH')
  printf "%-11s pass=%-3s fail=%s%s\n" "$t" "$n" "$f" "$([ "$c" != 0 ] && echo '  CRASHED')"
  echo "$out" | grep -E '^FAIL|^CRASH' && fail=1
done
exit $fail
