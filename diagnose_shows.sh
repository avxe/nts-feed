#!/bin/bash
# Diagnostic script to check shows.json status

echo "NTS Feed - Shows Diagnostic"
echo "========================================"
echo ""

# Check if running in Docker
if [ -f /.dockerenv ]; then
    echo "✓ Running inside Docker container"
    BASE_DIR="/app"
else
    echo "✓ Running on host system"
    BASE_DIR="."
fi

echo ""
echo "Checking for shows.json..."
echo "----------------------------------------"

# Check main file
if [ -f "$BASE_DIR/shows.json" ]; then
    SIZE=$(stat -f%z "$BASE_DIR/shows.json" 2>/dev/null || stat -c%s "$BASE_DIR/shows.json" 2>/dev/null)
    echo "✓ shows.json exists ($SIZE bytes)"
    
    # Try to validate JSON
    if command -v python3 &> /dev/null; then
        if python3 -c "import json; json.load(open('$BASE_DIR/shows.json'))" 2>/dev/null; then
            COUNT=$(python3 -c "import json; print(len(json.load(open('$BASE_DIR/shows.json'))))")
            echo "✓ Valid JSON with $COUNT shows"
        else
            echo "✗ CORRUPTED JSON - cannot parse"
            echo ""
            echo "Error details:"
            python3 -c "import json; json.load(open('$BASE_DIR/shows.json'))" 2>&1 | head -5
        fi
    else
        echo "⚠ Python3 not available for validation"
    fi
else
    echo "✗ shows.json NOT FOUND"
fi

echo ""
echo "Checking for backup..."
echo "----------------------------------------"

# Check backup
if [ -f "$BASE_DIR/shows.json.backup" ]; then
    SIZE=$(stat -f%z "$BASE_DIR/shows.json.backup" 2>/dev/null || stat -c%s "$BASE_DIR/shows.json.backup" 2>/dev/null)
    echo "✓ shows.json.backup exists ($SIZE bytes)"
    
    if command -v python3 &> /dev/null; then
        if python3 -c "import json; json.load(open('$BASE_DIR/shows.json.backup'))" 2>/dev/null; then
            COUNT=$(python3 -c "import json; print(len(json.load(open('$BASE_DIR/shows.json.backup'))))")
            echo "✓ Valid JSON with $COUNT shows"
        else
            echo "✗ Backup is also corrupted"
        fi
    fi
else
    echo "✗ No backup file found"
fi

echo ""
echo "Checking for corrupted backups..."
echo "----------------------------------------"

CORRUPTED=$(find "$BASE_DIR" -name "shows.json.corrupted.*" 2>/dev/null)
if [ -n "$CORRUPTED" ]; then
    echo "Found corrupted file backups:"
    echo "$CORRUPTED"
else
    echo "No corrupted file backups found"
fi

echo ""
echo "Checking Docker volume (if applicable)..."
echo "----------------------------------------"

if command -v docker &> /dev/null; then
    CONTAINER=$(docker ps --filter "name=nts" --format "{{.Names}}" | head -1)
    if [ -n "$CONTAINER" ]; then
        echo "✓ Found container: $CONTAINER"
        echo ""
        echo "Files in container:"
        docker exec "$CONTAINER" ls -lh /app/shows.json* 2>/dev/null || echo "No shows.json files in /app/"
    else
        echo "No running NTS container found"
    fi
else
    echo "Docker not available"
fi

echo ""
echo "========================================"
echo "Next steps:"
echo ""
echo "If shows.json is corrupted:"
echo "  1. Run: recover-shows"
echo "  2. Or manually restore from backup:"
echo "     cp shows.json.backup shows.json"
echo ""
echo "If no shows.json exists:"
echo "  1. Check Docker volume for the file"
echo "  2. Re-subscribe to your shows"
echo ""
echo "Check server logs for more details:"
echo "  docker logs <container-name>"
