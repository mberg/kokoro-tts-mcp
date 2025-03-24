#!/usr/bin/env python3
import os
import time
import json
import asyncio
from werkzeug.utils import secure_filename
import boto3
from botocore.exceptions import ClientError
import argparse
import uuid
from dotenv import load_dotenv
import datetime
import shutil

# Load environment variables from .env file
load_dotenv()

# Create default settings object to replace settings.py
class Settings:
    def __init__(self):
        self.S3_SETTINGS = {
            "enabled": False,  # Default to disabled
            "bucket_name": None,
            "region": None,
            "folder": "mp3",
            "access_key_id": None,
            "secret_access_key": None,
            "endpoint_url": None
        }

# Initialize settings object
settings = Settings()

# Function to check if running on Claude Desktop and load config
def load_claude_desktop_config():
    """
    Check if running on Claude Desktop and load config from claude_desktop_config.json
    Returns True if config was loaded from Claude Desktop, False otherwise
    """
    # Check if we're running on Claude Desktop (you might need to adjust this check)
    claude_desktop_config_path = os.path.expanduser("~/claude_desktop_config.json")
    
    if os.path.exists(claude_desktop_config_path):
        try:
            print(f"Claude Desktop config found at {claude_desktop_config_path}, loading...")
            with open(claude_desktop_config_path, 'r') as config_file:
                config = json.load(config_file)
                
            # Load environment variables from the config
            if 'environment' in config:
                for key, value in config['environment'].items():
                    os.environ[key] = str(value)
                    print(f"Loaded environment variable from Claude Desktop config: {key}")
            
            return True
        except Exception as e:
            print(f"Error loading Claude Desktop config: {e}")
    
    return False

# Try to load config from Claude Desktop first, fall back to .env if not found
if not load_claude_desktop_config():
    # Load environment variables from .env file
    print("Loading environment variables from .env file")
    load_dotenv()

# Import the fastMCP SDK (make sure it's installed and on your PYTHONPATH)
from mcp.server.fastmcp import FastMCP

# Only attempt to import the KokoroTTSService if it's available
try:
    from kokoro_service import KokoroTTSService
    # Initialize TTS service
    tts_service = KokoroTTSService()
    TTS_AVAILABLE = True
except ImportError:
    print("WARNING: kokoro_service module not found. TTS functionality will be disabled.")
    tts_service = None
    TTS_AVAILABLE = False

# Configure paths
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MP3_FOLDER = os.environ.get('MP3_FOLDER', os.path.join(ROOT_DIR, 'mp3'))
os.makedirs(MP3_FOLDER, exist_ok=True)

class MCPTTSServer:
    """
    Model Context Protocol (MCP) server for Kokoro TTS service.
    Processes JSON requests with text-to-speech parameters.

    Expected JSON request example:
    {
        "text": "Text to convert to speech",
        "voice": "voice_id",           # Optional; default: "af_heart"
        "speed": 1.0,                  # Optional; default: 1.0
        "lang": "en-us",               # Optional; default: "en-us"
        "filename": "output.mp3",      # Optional; a UUID will be generated if not provided
        "upload_to_s3": true           # Optional; defaults to true if S3 is enabled
    }
    
    The response will include file details and (if enabled) an S3 URL.
    """
    def __init__(self, host='0.0.0.0', port=9876):
        self.host = host
        self.port = port
        
        # Validate S3 settings at startup
        self.s3_enabled = False
        self.s3_client = None
        self.validate_s3_settings()
        
        # Clean up old MP3 files if retention period is set
        self.cleanup_old_mp3_files()
    
    def cleanup_old_mp3_files(self):
        """Clean up MP3 files older than the retention period."""
        retention_days_str = os.environ.get('MP3_RETENTION_DAYS')
        if not retention_days_str:
            return
            
        try:
            retention_days = int(retention_days_str)
            if retention_days <= 0:
                print(f"MP3 file retention disabled (MP3_RETENTION_DAYS={retention_days})")
                return
                
            print(f"Cleaning up MP3 files older than {retention_days} days...")
            now = datetime.datetime.now()
            cutoff_date = now - datetime.timedelta(days=retention_days)
            
            files_removed = 0
            for filename in os.listdir(MP3_FOLDER):
                if not filename.endswith('.mp3'):
                    continue
                    
                file_path = os.path.join(MP3_FOLDER, filename)
                file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
                
                if file_mtime < cutoff_date:
                    try:
                        os.remove(file_path)
                        files_removed += 1
                    except Exception as e:
                        print(f"Error removing old MP3 file {file_path}: {e}")
            
            if files_removed > 0:
                print(f"Removed {files_removed} MP3 files older than {retention_days} days")
            else:
                print(f"No MP3 files older than {retention_days} days found")
                
        except ValueError:
            print(f"Invalid MP3_RETENTION_DAYS value: {retention_days_str}")
        except Exception as e:
            print(f"Error during MP3 cleanup: {e}")
    
    def validate_s3_settings(self):
        """Validate S3 settings and create client if enabled."""
        try:
            # Check if S3 is explicitly disabled via environment variable
            if os.environ.get('DISABLE_S3', '').lower() in ('true', '1', 'yes'):
                print("S3 uploads are disabled via DISABLE_S3 environment variable")
                return
                
            # Check if S3 is explicitly enabled via environment variable
            s3_enabled_env = os.environ.get('S3_ENABLED', '').lower()
            if s3_enabled_env in ('false', '0', 'no'):
                print("S3 uploads are disabled via S3_ENABLED environment variable")
                return
                
            # If S3_ENABLED is not explicitly set to true, disable S3
            if not (s3_enabled_env in ('true', '1', 'yes')):
                print("S3 uploads are disabled (S3_ENABLED not set to true)")
                return
            
            print("Validating S3 settings...")
            # Get settings from environment variables
            bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
            region = os.environ.get('AWS_S3_REGION')
            folder = os.environ.get('AWS_S3_FOLDER', 'mp3')
            endpoint_url = os.environ.get('AWS_S3_ENDPOINT_URL')
            
            if not bucket_name:
                print("ERROR: S3 bucket name is not configured (AWS_S3_BUCKET_NAME)")
                return
                
            if not region:
                print("ERROR: S3 region is not configured (AWS_S3_REGION)")
                return
            
            try:
                # Get AWS credentials from environment variables
                aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
                if not aws_access_key_id:
                    print("ERROR: AWS_ACCESS_KEY_ID not found in environment (.env)")
                    return
                else:
                    print("Using AWS access key from environment variables (.env)")
                
                aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
                if not aws_secret_access_key:
                    print("ERROR: AWS_SECRET_ACCESS_KEY not found in environment (.env)")
                    return
                else:
                    print("Using AWS secret key from environment variables (.env)")
                
                print(f"Creating S3 client with: region={region}, endpoint_url={endpoint_url}")
                session = boto3.Session(
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    region_name=region
                )
                
                self.s3_client = session.client(
                    's3',
                    endpoint_url=endpoint_url
                )
                
                print(f"Testing S3 connection to bucket '{bucket_name}'...")
                self.s3_client.head_bucket(Bucket=bucket_name)
                
                print("✅ S3 connection validated successfully")
                self.s3_enabled = True
                print(f"S3 uploads enabled to bucket: {bucket_name}")
                print(f"S3 folder: {folder}")
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code')
                if error_code == '403':
                    print(f"ERROR: No permission to access S3 bucket '{bucket_name}'")
                elif error_code == '404':
                    print(f"ERROR: S3 bucket '{bucket_name}' does not exist")
                else:
                    print(f"ERROR: S3 client failed: {str(e)}")
                import traceback
                traceback.print_exc()
                return
                
        except Exception as e:
            print(f"ERROR initializing S3 settings: {str(e)}")
            import traceback
            traceback.print_exc()
            
    def upload_to_s3(self, file_path, object_name=None):
        """Upload a file to the S3 bucket and return the file's URL."""
        print(f"Starting S3 upload process for {file_path}")
        if not self.s3_enabled or not self.s3_client:
            print("S3 uploads are disabled or failed to initialize")
            return None
        
        if object_name is None:
            object_name = os.path.basename(file_path)
        
        # Get settings from environment variables
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        folder = os.environ.get('AWS_S3_FOLDER', 'mp3')
        
        if folder and not folder.endswith('/'):
            folder += '/'
        
        s3_path = folder + object_name
        print(f"Uploading to S3: bucket={bucket_name}, key={s3_path}")
        
        try:
            print(f"Uploading file {file_path} to S3...")
            self.s3_client.upload_file(file_path, bucket_name, s3_path)
            print("✅ File successfully uploaded to S3")
            
            # Get endpoint URL and region from environment variables
            endpoint = os.environ.get('AWS_S3_ENDPOINT_URL')
            region = os.environ.get('AWS_S3_REGION')
            
            if endpoint:
                s3_url = f"{endpoint}/{bucket_name}/{s3_path}"
            else:
                s3_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_path}"
                
            print(f"Generated S3 URL: {s3_url}")
            return s3_url
            
        except ClientError as e:
            print(f"ERROR: S3 upload failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
    
    async def process_tts_request(self, request_data):
        """Process a TTS request and return a JSON response."""
        try:
            if not TTS_AVAILABLE:
                return {
                    "success": False,
                    "error": "TTS service is not available. Missing required modules."
                }
                
            text = request_data.get('text', '')
            voice = request_data.get('voice', os.environ.get('TTS_VOICE', 'af_heart'))
            speed = float(request_data.get('speed', 1.0))
            lang = request_data.get('lang', 'en-us')
            filename = request_data.get('filename', None)
            upload_to_s3_flag = request_data.get('upload_to_s3', True)
            
            if not text:
                return {"success": False, "error": "No text provided"}
            
            if not filename:
                filename = str(uuid.uuid4())
            
            if not filename.endswith('.mp3'):
                filename += '.mp3'
                
            filename = secure_filename(filename)
            os.makedirs(MP3_FOLDER, exist_ok=True)
            mp3_path = os.path.join(MP3_FOLDER, filename)
            mp3_filename = os.path.basename(mp3_path)
            
            print(f"Generating audio for: {text[:50]}{'...' if len(text) > 50 else ''}")
            print(f"Using voice: {voice}, speed: {speed}, language: {lang}")
            print(f"Output file: {mp3_path}")
            
            loop = asyncio.get_running_loop()
            
            try:
                # Attempt primary parameter format
                result = await loop.run_in_executor(
                    None, 
                    lambda: tts_service.generate_audio(
                        text=text, 
                        output_file=mp3_path, 
                        voice=voice,
                        speed=speed,
                        lang=lang
                    )
                )
                
                if isinstance(result, dict) and not result.get('success', True):
                    print(f"TTS service returned an error: {result}")  # Log the result for debugging
                    return {
                        "success": False,
                        "error": result.get('error', 'Unknown TTS generation error'),
                        "tts_result": result,  # Include full TTS service response
                        "request_params": {
                            "text": text,
                            "voice": voice,
                            "speed": speed,
                            "lang": lang,
                            "filename": filename
                        },
                        "timestamp": datetime.datetime.now().isoformat()
                    }
                    
            except TypeError as e:
                print(f"TypeError in TTS service call: {e}")
                print("Trying alternative parameter format...")
                result = await loop.run_in_executor(
                    None, 
                    lambda: tts_service.generate_audio(
                        text, 
                        mp3_path, 
                        voice=voice,
                        speed=speed
                    )
                )
            
            if not os.path.exists(mp3_path):
                return {
                    "success": False,
                    "error": "Failed to generate audio file"
                }
                
            file_size = os.path.getsize(mp3_path)
            print(f"Audio generated successfully. File size: {file_size} bytes")
            
            response_data = {
                "success": True,
                "message": "Audio generated successfully",
                "filename": mp3_filename,
                "file_size": file_size,
                "path": mp3_path,
                "s3_uploaded": False
            }
            
            if upload_to_s3_flag:
                print(f"Uploading {mp3_filename} to S3...")
                s3_url = self.upload_to_s3(mp3_path, mp3_filename)
                if s3_url:
                    response_data["s3_uploaded"] = True
                    response_data["s3_url"] = s3_url
                    
                    # Delete local file if configured to do so
                    if os.environ.get('DELETE_LOCAL_AFTER_S3_UPLOAD', '').lower() in ('true', '1', 'yes'):
                        try:
                            print(f"Removing local file {mp3_path} after successful S3 upload")
                            os.remove(mp3_path)
                            response_data["local_file_kept"] = False
                        except Exception as e:
                            print(f"Error removing local file after S3 upload: {e}")
                            response_data["local_file_kept"] = True
                    else:
                        response_data["local_file_kept"] = True
                else:
                    response_data["s3_uploaded"] = False
                    response_data["s3_error"] = "S3 upload failed"
            
            return response_data
            
        except Exception as e:
            print(f"Error processing TTS request: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }

def main():
    parser = argparse.ArgumentParser(description="MCP TTS Server")
    parser.add_argument("--host", default=os.environ.get('MCP_HOST', '0.0.0.0'),
                        help="Host to bind the server to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get('MCP_PORT', 9876)),
                        help="Port to listen on (default: 9876)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug mode with additional logging")
    parser.add_argument("--disable-s3", action="store_true",
                        help="Disable S3 uploads regardless of settings")
    parser.add_argument("--s3-access-key", 
                        help="Override S3 access key ID")
    parser.add_argument("--s3-secret-key",
                        help="Override S3 secret access key")
    parser.add_argument("--s3-bucket",
                        help="Override S3 bucket name")
    parser.add_argument("--s3-region",
                        help="Override S3 region")
    parser.add_argument("--claude-desktop-config", type=str,
                        help="Path to claude_desktop_config.json if not in default location")
    
    args = parser.parse_args()
    
    # If a custom claude desktop config path was provided, try to load it
    if args.claude_desktop_config and os.path.exists(args.claude_desktop_config):
        try:
            print(f"Loading custom Claude Desktop config from: {args.claude_desktop_config}")
            with open(args.claude_desktop_config, 'r') as config_file:
                config = json.load(config_file)
                
            # Load environment variables from the config
            if 'environment' in config:
                for key, value in config['environment'].items():
                    os.environ[key] = str(value)
                    print(f"Loaded environment variable from custom Claude Desktop config: {key}")
        except Exception as e:
            print(f"Error loading custom Claude Desktop config: {e}")
    
    # Check for DISABLE_S3 environment variable
    if os.environ.get('DISABLE_S3') == 'true' or os.environ.get('DISABLE_S3') == '1':
        os.environ['S3_ENABLED'] = 'false'
        print("S3 uploads disabled by environment variable")
    elif args.disable_s3:
        os.environ['S3_ENABLED'] = 'false'
        print("S3 uploads disabled by command line argument")
    
    # Command line args override environment variables
    if args.s3_access_key:
        os.environ['AWS_ACCESS_KEY_ID'] = args.s3_access_key
        print("Using S3 access key from command line")
    
    if args.s3_secret_key:
        os.environ['AWS_SECRET_ACCESS_KEY'] = args.s3_secret_key
        print("Using S3 secret key from command line")
    
    if args.s3_bucket:
        os.environ['AWS_S3_BUCKET_NAME'] = args.s3_bucket
        print(f"Using S3 bucket from command line: {args.s3_bucket}")
    
    if args.s3_region:
        os.environ['AWS_S3_REGION'] = args.s3_region
        print(f"Using S3 region from command line: {args.s3_region}")
    
    # Print debug information about loaded environment variables if debug mode is enabled
    if args.debug or os.environ.get('DEBUG') == 'true' or os.environ.get('DEBUG') == '1':
        print("Debug mode enabled")
        print("Environment variables:")
        for var in ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_S3_BUCKET_NAME', 'AWS_S3_REGION', 'AWS_S3_FOLDER', 'AWS_S3_ENDPOINT_URL']:
            if os.environ.get(var):
                print(f"  {var}: {'*' * 10 if 'KEY' in var or 'SECRET' in var else os.environ.get(var)}")
            else:
                print(f"  {var}: Not set")
    
    # Instantiate our TTS server (which handles S3 validation and TTS generation)
    mcp_tts_server = MCPTTSServer(host=args.host, port=args.port)
    
    # Create and configure the FastMCP server
    mcp = FastMCP("Kokoro TTS Server")
    
    # Register our TTS tool using the decorator syntax
    @mcp.tool()
    async def text_to_speech(text: str, voice: str = os.environ.get('TTS_VOICE', 'af_heart'), 
                            speed: float = float(os.environ.get('TTS_SPEED', 1.0)), 
                            lang: str = os.environ.get('TTS_LANGUAGE', 'en-us'),
                            filename: str = None,
                            upload_to_s3: bool = os.environ.get('S3_ENABLED', 'true').lower() == 'true') -> dict:
        """
        Convert text to speech using the Kokoro TTS service.
        
        Args:
            text: The text to convert to speech
            voice: Voice ID to use (default: af_heart)
            speed: Speech speed (default: 1.0)
            lang: Language code (default: en-us)
            filename: Optional filename for the MP3 (default: auto-generated UUID)
            upload_to_s3: Whether to upload to S3 if enabled (default: True)
            
        Returns:
            A dictionary with information about the generated audio file
        """
        request_data = {
            "text": text,
            "voice": voice,
            "speed": speed,
            "lang": lang,
            "filename": filename,
            "upload_to_s3": upload_to_s3
        }
        
        return await mcp_tts_server.process_tts_request(request_data)
    
    print(f"Starting MCP TTS Server on {args.host}:{args.port}")
    print(f"MP3 files will be stored in: {MP3_FOLDER}")
    
    try:
        # Run the server
        mcp.run()
    except KeyboardInterrupt:
        print("Server stopped by user")
    except Exception as e:
        print(f"Server error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()