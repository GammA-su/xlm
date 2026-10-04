set -e
S=/tmp/claude-0/-home-user-xlm/30057eef-b37d-5aef-8ce7-15e24f59d6c4/scratchpad
cd /home/user/xlm
rm -rf $S/cmp512
.venv/bin/python -c "from pathlib import Path; from xlm.data.quality.bench import build_corpus; build_corpus(Path('$S/cmp512'), 512*2**20)"
for w in 1 2 4 8; do
  M=$S/cmp512/manifest.json
  (cd $S/old && PYTHONPATH=$S/old/src /home/user/xlm/.venv/bin/python $S/cmp_run.py $M $S/cmpout_old $w 2>/dev/null | sed 's/^/old /')
  .venv/bin/python $S/cmp_run.py $M $S/cmpout_new $w 2>/dev/null | sed 's/^/new /'
done
