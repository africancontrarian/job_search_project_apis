#!/usr/bin/env bash
# Sends N requests through the load balancer and counts which server answered each one.
# Usage: ./deploy/test_lb.sh http://13.40.99.74 10

URL="${1:-http://localhost}"
COUNT="${2:-10}"

echo "Sending $COUNT requests to $URL/api/health"
echo

for i in $(seq 1 "$COUNT"); do
  server=$(curl -s -D - -o /dev/null "$URL/api/health" | grep -i '^x-served-by:' | awk '{print $2}' | tr -d '\r')
  echo "Request $i -> ${server:-NO RESPONSE}"
  echo "${server:-NO_RESPONSE}" >> /tmp/lb_test_results.$$
done

echo
echo "Summary:"
sort /tmp/lb_test_results.$$ | uniq -c
rm -f /tmp/lb_test_results.$$
