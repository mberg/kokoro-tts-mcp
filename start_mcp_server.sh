#!/bin/bash
# Script to start the MCP TTS server

# Determine the Python command to use
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "Error: No python or python3 command found in PATH"
    exit 1
fi

# Default port
PORT=${MCP_PORT:-9876}
HOST=${MCP_HOST:-0.0.0.0}
DEBUG=""
S3_FLAGS=""

# Display help message
function show_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  -h, --help          Show this help message"
    echo "  -p, --port PORT     Set the server port (default: 9876)"
    echo "  -b, --bind HOST     Set the bind address (default: 0.0.0.0)"
    echo "  -d, --debug         Enable debug mode with additional logging"
    echo "  --disable-s3        Disable S3 uploads regardless of settings"
    echo "  --s3-access-key KEY Override S3 access key ID"
    echo "  --s3-secret-key KEY Override S3 secret access key"
    echo "  --s3-bucket NAME    Override S3 bucket name"
    echo "  --s3-region REGION  Override S3 region"
    echo ""
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -b|--bind)
            HOST="$2"
            shift 2
            ;;
        -d|--debug)
            DEBUG="--debug"
            shift
            ;;
        --disable-s3)
            S3_FLAGS="$S3_FLAGS --disable-s3"
            shift
            ;;
        --s3-access-key)
            S3_FLAGS="$S3_FLAGS --s3-access-key $2"
            shift 2
            ;;
        --s3-secret-key)
            S3_FLAGS="$S3_FLAGS --s3-secret-key $2"
            shift 2
            ;;
        --s3-bucket)
            S3_FLAGS="$S3_FLAGS --s3-bucket $2"
            shift 2
            ;;
        --s3-region)
            S3_FLAGS="$S3_FLAGS --s3-region $2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

echo "Starting MCP TTS Server..."
echo "Using Python: $PYTHON_CMD"
echo "Host: $HOST"
echo "Port: $PORT"
if [ -n "$DEBUG" ]; then
    echo "Debug mode: enabled"
fi

# Start the server
$PYTHON_CMD mcp_tts_server.py --host "$HOST" --port "$PORT" $DEBUG $S3_FLAGS

# Check exit code
if [ $? -ne 0 ]; then
    echo "Error: MCP TTS Server failed to start"
    exit 1
fi

# If the server process exits, print a message
echo "MCP TTS Server stopped." 