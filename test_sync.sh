#!/bin/bash

echo "Calling sync endpoint..."
echo ""

RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" http://127.0.0.1:8021/api/sync-aptem-users/)
HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS:" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | grep -v "HTTP_STATUS:")

echo "HTTP Status: $HTTP_STATUS"
echo "Response:"
echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
