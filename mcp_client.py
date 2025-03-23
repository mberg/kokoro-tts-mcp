#!/usr/bin/env python3

import argparse
import asyncio
import json
import os
import sys
import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

async def send_tts_request(host, port, text, voice, speed, language, filename, upload_to_s3=True):
    """Send a TTS request to the MCP server"""
    try:
        # Connect to the server
        reader, writer = await asyncio.open_connection(host, port)
        
        # Create the request
        request = {
            "text": text,
            "voice": voice,
            "speed": speed,
            "lang": language,
            "upload_to_s3": upload_to_s3
        }
        
        # Add filename if provided
        if filename:
            request["filename"] = filename
        
        # Encode the request as JSON
        request_json = json.dumps(request)
        
        # Send the request
        print(f"Sending request to {host}:{port}...")
        print(f"Request: {request_json}")
        writer.write(request_json.encode('utf-8'))
        await writer.drain()
        
        # Read the response
        print("Waiting for response...")
        data = await reader.read(4096)
        response = json.loads(data.decode('utf-8'))
        
        # Close the connection
        writer.close()
        await writer.wait_closed()
        
        # Return the response
        return response
    
    except ConnectionRefusedError:
        print(f"Error: Could not connect to MCP TTS Server at {host}:{port}")
        print("Please make sure the server is running.")
        return None
    except Exception as e:
        print(f"Error: {str(e)}")
        return None

def print_response(response):
    """Print the server response in a human-readable format"""
    if response is None:
        return
    
    print("\n=== TTS Response ===")
    if response.get("success", False):
        print("✅ Success: Audio generated successfully")
        print(f"📁 Filename: {response.get('filename', 'Unknown')}")
        print(f"📊 File size: {response.get('file_size', 0)} bytes")
        
        if response.get("s3_uploaded", False):
            print("☁️ S3 Upload: Success")
            print(f"🔗 S3 URL: {response.get('s3_url', 'Not available')}")
        elif "s3_uploaded" in response and not response["s3_uploaded"]:
            print("☁️ S3 Upload: Failed")
            print(f"❌ Error: {response.get('s3_error', 'Unknown error')}")
        else:
            print("☁️ S3 Upload: Not requested")
    else:
        print("❌ Error: TTS generation failed")
        print(f"Error message: {response.get('error', 'Unknown error')}")
    
    print("=====================\n")

def read_text_file(file_path):
    """Read text from a file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        print(f"Error reading file {file_path}: {str(e)}")
        sys.exit(1)

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="MCP TTS Client")
    
    # Server connection - Important: for connecting to a local server, use "localhost" or "127.0.0.1", not "0.0.0.0"
    parser.add_argument("--host", default=os.environ.get("MCP_CLIENT_HOST", "localhost"),
                       help="MCP server hostname (default: localhost or MCP_CLIENT_HOST env var)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "9876")),
                       help="MCP server port (default: 9876 or MCP_PORT env var)")
    
    # TTS parameters
    parser.add_argument("--text", default=None,
                       help="Text to synthesize")
    parser.add_argument("--file", default=None,
                       help="Text file to read content from")
    parser.add_argument("--voice", default=os.environ.get("TTS_VOICE", "af_heart"),
                       help="Voice to use (default: af_heart or TTS_VOICE env var)")
    parser.add_argument("--speed", type=float, default=float(os.environ.get("TTS_SPEED", "1.0")),
                       help="Speech speed (default: 1.0 or TTS_SPEED env var)")
    parser.add_argument("--language", default=os.environ.get("TTS_LANGUAGE", "en-us"),
                       help="Language code (default: en-us or TTS_LANGUAGE env var)")
    parser.add_argument("--filename", default=None,
                       help="Output filename (default: auto-generated)")
    
    # S3 options
    parser.add_argument("--no-s3", action="store_true",
                       help="Disable S3 upload (enabled by default)")
    
    # Debug options
    parser.add_argument("--raw", action="store_true",
                       help="Print raw JSON response")
    
    args = parser.parse_args()
    
    # Check for text input
    if args.text is None and args.file is None:
        parser.error("Either --text or --file is required")
    
    # Read text from file if specified
    if args.file:
        text = read_text_file(args.file)
    else:
        text = args.text
    
    # Run the request
    response = asyncio.run(send_tts_request(
        host=args.host,
        port=args.port,
        text=text,
        voice=args.voice,
        speed=args.speed,
        language=args.language,
        filename=args.filename,
        upload_to_s3=not args.no_s3
    ))
    
    # Print the response
    if response:
        if args.raw:
            print(json.dumps(response, indent=2))
        else:
            print_response(response)

if __name__ == "__main__":
    main() 