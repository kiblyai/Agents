#!/bin/sh
# Runs the synthetic example of each agent below with your model settings (.env), then scores it against the
# answer key. Everything printed is also saved to out/examples.log, which is the file to send back.
#
#   sh examples/run_examples.sh                    all seven (about 39 model requests in all)
#   sh examples/run_examples.sh upworkbid grants   just these
#
# Free models allow 50 requests a day (1,000 after $10 of credit). If the limit stops a run, run the script
# again after it resets: finished requests come from the cache.

cd "$(dirname "$0")/.." || exit 1
if [ -z "$RUN_EXAMPLES_LOGGING" ]; then
  mkdir -p out
  RUN_EXAMPLES_LOGGING=1 sh examples/run_examples.sh "$@" 2>&1 | tee out/examples.log
  echo
  echo "Saved to out/examples.log"
  exit
fi

if [ -n "$PYTHON" ]; then
  PY=$PYTHON
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi
AGENTS=${*:-"outreach shopifypo grants txcoord crmclean govbids upworkbid"}
EX=examples
DAY=2026-10-12

run() {
  printf '$ python %s\n' "$*"
  "$PY" "$@" || echo "(stopped with exit code $?)"
}

for a in $AGENTS; do
  printf '\n========== %s ==========\n' "$a"
  OUT=out/$a/sample
  case $a in
    outreach)
      run -m outreach write --campaign $EX/outreach/campaign.toml --leads $EX/outreach/leads.csv --out $OUT
      run -m outreach replies --campaign $EX/outreach/campaign.toml --leads $EX/outreach/leads.csv \
        --replies $EX/outreach/replies --out $OUT ;;
    shopifypo)
      run -m shopifypo run --emails $EX/shopifypo/inbox --catalog $EX/shopifypo/catalog.csv \
        --prices $EX/shopifypo/prices.csv --customers $EX/shopifypo/customers.csv --shop $EX/shopifypo/shop.toml \
        --out $OUT ;;
    grants)
      G="--org $EX/grants/org.toml --funders $EX/grants/funders.csv --guidelines $EX/grants/guidelines --today $DAY"
      run -m grants prospect $G --out $OUT
      run -m grants draft $G --request $EX/grants/request.toml --docs $EX/grants/docs --budget $EX/grants/budget.csv \
        --out $OUT ;;
    txcoord)
      run -m txcoord run --files $EX/txcoord/transactions --tc $EX/txcoord/tc.toml --today $DAY --out $OUT ;;
    crmclean)
      run -m crmclean run --contacts $EX/crmclean/contacts.csv --companies $EX/crmclean/companies.csv \
        --crm $EX/crmclean/crm.toml --today $DAY --sites $EX/crmclean/sites --out $OUT ;;
    govbids)
      run -m govbids match --company $EX/govbids/company.toml --notices $EX/govbids/notices.csv --today $DAY --out $OUT
      run -m govbids shred --rfp $EX/govbids/rfp --out $OUT ;;
    upworkbid)
      run -m upworkbid run --profile $EX/upworkbid/profile.toml --jobs $EX/upworkbid/jobs --today $DAY --out $OUT ;;
    *)
      echo "No example for '$a'. Choose from: outreach shopifypo grants txcoord crmclean govbids upworkbid"
      continue ;;
  esac
  echo
  run -m $a score --run $OUT
done
