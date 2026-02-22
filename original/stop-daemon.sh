#!/bin/bash
[[ "$(uname -o 2>/dev/null)" == *"Msys"* ]] && echo "NOTE: On Windows, prefer: py manager.py"

echo "Stopping Claude Auto Renew daemon..."

# Kill all instances of the daemon script
pkill -f "claude-auto-renew-daemon.sh"

# Wait a moment for processes to terminate
sleep 1

# Check if any processes are still running
remaining=$(ps aux | grep -i "claude-auto-renew-daemon" | grep -v grep | wc -l)

if [ "$remaining" -eq 0 ]; then
    echo "Daemon stopped successfully."
else
    echo "Warning: $remaining daemon process(es) still running."
    echo "Trying force kill..."
    pkill -9 -f "claude-auto-renew-daemon.sh"
    sleep 1
    
    # Final check
    remaining=$(ps aux | grep -i "claude-auto-renew-daemon" | grep -v grep | wc -l)
    if [ "$remaining" -eq 0 ]; then
        echo "Daemon force stopped successfully."
    else
        echo "Error: Unable to stop all daemon processes."
    fi
fi