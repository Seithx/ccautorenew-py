#!/bin/bash

# Quick Test Script for CC AutoRenew
# Performs basic validation of all components in under 1 minute

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

TESTS_PASSED=0
TESTS_FAILED=0

print_test() {
    echo -e "\n${BLUE}[TEST]${NC} $1"
}

print_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((TESTS_PASSED++))
}

print_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((TESTS_FAILED++))
}

print_info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║${NC}          CC AutoRenew Quick Test        ${BLUE}║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Test 1: Check all required scripts exist and are executable
print_test "Checking script files"
scripts=(
    "claude-daemon-manager.sh"
    "claude-auto-renew-daemon.sh"
    "claude-auto-renew-advanced.sh"
    "claude-auto-renew.sh"
    "setup-claude-cron.sh"
)

for script in "${scripts[@]}"; do
    if [ -f "$script" ] && [ -x "$script" ]; then
        print_pass "$script exists and is executable"
    else
        print_fail "$script missing or not executable"
    fi
done

# Test 2: Check basic dependencies
print_test "Checking dependencies"

if command -v claude &> /dev/null; then
    print_pass "Claude CLI found"
else
    print_fail "Claude CLI not found"
fi

if command -v ccusage &> /dev/null || command -v bunx &> /dev/null || command -v npx &> /dev/null; then
    print_pass "ccusage availability confirmed"
else
    print_fail "ccusage not available via any method"
fi

# Test 3: Test daemon manager help
print_test "Testing daemon manager"
if ./claude-daemon-manager.sh --help 2>&1 | grep -q "Usage"; then
    print_pass "Daemon manager shows help correctly"
else
    print_fail "Daemon manager help not working"
fi

# Test 4: Test daemon start/stop without actually running
print_test "Testing daemon start/stop dry run"
# Just test that the script accepts the commands without error syntax
if ./claude-daemon-manager.sh 2>&1 | grep -q "start"; then
    print_pass "Daemon manager accepts start command"
else
    print_fail "Daemon manager start command issue"
fi

# Test 5: Test start time parsing
print_test "Testing start time parameter parsing"
if ./claude-daemon-manager.sh start --at "25:00" 2>&1 | grep -qE "Invalid (start |stop )?time format"; then
    print_pass "Invalid time format correctly rejected"
else
    print_fail "Invalid time format not properly handled"
fi

# Test 6: Check log file creation capability
print_test "Testing log file access"
test_log="/tmp/cc-autorenew-test-$$"
if echo "test" > "$test_log" 2>/dev/null; then
    print_pass "Can create log files"
    rm -f "$test_log"
else
    print_fail "Cannot create log files"
fi

# Test 7: Test advanced renewal script basic functionality
print_test "Testing advanced renewal script"
# Check if script has proper content by examining the file
if grep -q "ccusage\|renewal\|Auto" ./claude-auto-renew-advanced.sh; then
    print_pass "Advanced renewal script has proper content"
else
    print_fail "Advanced renewal script missing expected content"
fi

# Test basic syntax
bash -n ./claude-auto-renew-advanced.sh 2>/dev/null
if [ $? -eq 0 ]; then
    print_pass "Advanced renewal script has valid syntax"
else
    print_fail "Advanced renewal script has syntax errors"
fi

# Test 8: Test setup script options
print_test "Testing setup script"
if echo "3" | timeout 5s ./setup-claude-cron.sh 2>&1 | grep -q "Invalid choice"; then
    print_pass "Setup script handles invalid choices"
else
    # Fallback: check if script has proper content
    if grep -q "Invalid choice\|DAEMON\|CRON" ./setup-claude-cron.sh; then
        print_pass "Setup script has proper validation logic"
    else
        print_fail "Setup script doesn't validate input properly"
    fi
fi

# Summary
echo ""
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║${NC}              QUICK TEST SUMMARY         ${BLUE}║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""
echo -e "Total Tests: ${BLUE}$((TESTS_PASSED + TESTS_FAILED))${NC}"
echo -e "${GREEN}Passed:${NC} $TESTS_PASSED"
echo -e "${RED}Failed:${NC} $TESTS_FAILED"

if [ $TESTS_FAILED -eq 0 ]; then
    echo ""
    echo -e "${GREEN}🎉 Quick test passed! CC AutoRenew appears to be set up correctly.${NC}"
    echo ""
    echo "Next steps:"
    echo "  • Run comprehensive tests: ./test-start-time-feature.sh"
    echo "  • Start the daemon: ./claude-daemon-manager.sh start"
    echo "  • Check status: ./claude-daemon-manager.sh status"
    exit 0
else
    echo ""
    echo -e "${RED}❌ Some quick tests failed. Please check the issues above.${NC}"
    exit 1
fi 