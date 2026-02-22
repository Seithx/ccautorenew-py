#!/bin/bash

# Setup script for Claude auto-renewal daemon
# This script helps users choose between daemon and cron approaches

DAEMON_MANAGER="$(cd "$(dirname "$0")" && pwd)/claude-daemon-manager.sh"
OLD_SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/claude-auto-renew.sh"
CRON_LOG="$HOME/.claude-cron-setup.log"

echo "Claude Auto-Renewal Setup"
echo "========================="
echo ""
echo "You have two options for running CC AutoRenew:"
echo ""
echo "1. 🚀 DAEMON MODE (Recommended)"
echo "   - Runs continuously in background"
echo "   - More accurate timing"
echo "   - Better monitoring and logging"
echo "   - Supports scheduled start times"
echo ""
echo "2. ⏰ CRON MODE (Legacy)"
echo "   - Uses system cron scheduler"
echo "   - Checks every 30 minutes"
echo "   - Simpler but less precise"
echo ""

read -p "Which mode would you prefer? (1 for Daemon, 2 for Cron): " choice

case "$choice" in
    1)
        echo ""
        echo "Setting up DAEMON mode..."
        
        # Check if daemon manager exists
        if [ ! -f "$DAEMON_MANAGER" ]; then
            echo "ERROR: claude-daemon-manager.sh not found at $DAEMON_MANAGER"
            exit 1
        fi
        
        # Ask about start time
        echo ""
        echo "Do you want to set a specific start time? (optional)"
        echo "Examples: '09:00' or '2025-01-28 14:30'"
        read -p "Start time (press Enter for immediate start): " start_time
        
        # Remove any existing cron jobs first
        if crontab -l 2>/dev/null | grep -q "claude-auto-renew"; then
            echo "Removing existing cron jobs..."
            crontab -l 2>/dev/null | grep -v "claude-auto-renew" | crontab -
        fi
        
        # Start the daemon
        if [ -n "$start_time" ]; then
            "$DAEMON_MANAGER" start --at "$start_time"
        else
            "$DAEMON_MANAGER" start
        fi
        
        if [ $? -eq 0 ]; then
            echo ""
            echo "✅ Daemon setup complete!"
            echo ""
            echo "Useful commands:"
            echo "  Status:  $DAEMON_MANAGER status"
            echo "  Logs:    $DAEMON_MANAGER logs -f"
            echo "  Stop:    $DAEMON_MANAGER stop"
            echo "  Restart: $DAEMON_MANAGER restart"
        else
            echo "❌ Failed to start daemon"
            exit 1
        fi
        ;;
        
    2)
        echo ""
        echo "Setting up CRON mode..."
        
        # Check if old script exists
        if [ ! -f "$OLD_SCRIPT_PATH" ]; then
            echo "ERROR: claude-auto-renew.sh not found at $OLD_SCRIPT_PATH"
            exit 1
        fi
        
        # Stop daemon if running
        if [ -f "$HOME/.claude-auto-renew-daemon.pid" ]; then
            echo "Stopping existing daemon..."
            "$DAEMON_MANAGER" stop 2>/dev/null
        fi
        
        # Create a temporary file for the new crontab
        TEMP_CRON=$(mktemp)
        
        # Get existing crontab (if any)
        crontab -l 2>/dev/null > "$TEMP_CRON" || true
        
        # Check if our cron job already exists
        if grep -q "claude-auto-renew" "$TEMP_CRON"; then
            echo "Existing Claude cron job found. Updating..."
            # Remove existing entries
            grep -v "claude-auto-renew" "$TEMP_CRON" > "${TEMP_CRON}.new"
            mv "${TEMP_CRON}.new" "$TEMP_CRON"
        fi
        
        # Add our cron job - run every 30 minutes
        echo "*/30 * * * * $OLD_SCRIPT_PATH >> $HOME/.claude-cron.log 2>&1" >> "$TEMP_CRON"
        
        # Install the new crontab
        crontab "$TEMP_CRON"
        
        # Clean up
        rm -f "$TEMP_CRON"
        
        echo ""
        echo "✅ Cron setup complete!"
        echo ""
        echo "The script will run every 30 minutes to check for renewals."
        echo ""
        echo "Logs will be written to:"
        echo "  - Main log: $HOME/.claude-auto-renew.log"
        echo "  - Cron log: $HOME/.claude-cron.log"
        echo ""
        echo "Useful commands:"
        echo "  View cron job: crontab -l | grep claude"
        echo "  Remove cron:   crontab -l | grep -v claude-auto-renew | crontab -"
        echo "  Test script:   $OLD_SCRIPT_PATH"
        ;;
        
    *)
        echo "Invalid choice. Please run the script again and choose 1 or 2."
        exit 1
        ;;
esac

echo ""
echo "🎉 Setup complete! CC AutoRenew is now configured and running."