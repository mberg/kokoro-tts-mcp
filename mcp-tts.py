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
import subprocess

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

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    edge_tts = None
    EDGE_TTS_AVAILABLE = False

# Configure paths
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MP3_FOLDER = os.environ.get('MP3_FOLDER', os.path.join(ROOT_DIR, 'mp3'))
os.makedirs(MP3_FOLDER, exist_ok=True)


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def speed_to_edge_rate(speed: float) -> str:
    percent = int(round((speed - 1.0) * 100))
    percent = max(-80, min(150, percent))
    if percent >= 0:
        return f"+{percent}%"
    return f"{percent}%"

class MCPTTSServer:
    """
    Model Context Protocol (MCP) server for Kokoro TTS service.
    Processes JSON requests with text-to-speech parameters.

    Expected JSON request example:
    {
        "text": "Text to convert to speech",
        "provider": "auto|edge|kokoro", # Optional; default: "auto"
        "voice": "voice_id",           # Optional; default: "af_heart"
        "speed": 1.0,                  # Optional; default: 1.0
        "lang": "en-us",               # Optional; default: "en-us"
        "filename": "output.mp3",      # Optional; a UUID will be generated if not provided
        "play_audio": true,            # Optional; play on default output device
        "stream_playback": true,       # Optional; for edge provider, stream while generating
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

    async def generate_with_edge_tts(self, text: str, voice: str, speed: float, output_path: str) -> dict:
        """Generate MP3 via edge-tts."""
        if not EDGE_TTS_AVAILABLE:
            return {
                "success": False,
                "error": "edge-tts module is not available"
            }
        try:
            rate = speed_to_edge_rate(speed)
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
            await communicate.save(output_path)
            if not os.path.exists(output_path):
                return {
                    "success": False,
                    "error": "edge-tts did not produce output file"
                }
            return {"success": True, "provider": "edge", "rate": rate}
        except Exception as e:
            return {"success": False, "error": f"edge-tts failed: {str(e)}"}

    async def stream_edge_tts_to_player(self, text: str, voice: str, speed: float, output_path: str | None = None) -> dict:
        """Stream edge-tts audio chunks directly to ffplay and optionally save an MP3 copy."""
        if not EDGE_TTS_AVAILABLE:
            return {
                "success": False,
                "error": "edge-tts module is not available"
            }

        ffplay_path = shutil.which("ffplay")
        if not ffplay_path:
            return {
                "success": False,
                "error": "ffplay not found in PATH. Install ffmpeg to use stream playback."
            }

        file_handle = None
        player = None
        try:
            rate = speed_to_edge_rate(speed)
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)

            if output_path:
                file_handle = open(output_path, "wb")

            player = subprocess.Popen(
                [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"],
                stdin=subprocess.PIPE
            )

            async for chunk in communicate.stream():
                if chunk.get("type") != "audio":
                    continue
                data = chunk.get("data", b"")
                if not data:
                    continue

                if file_handle is not None:
                    file_handle.write(data)

                if player.stdin is not None:
                    player.stdin.write(data)
                    player.stdin.flush()

            if player.stdin is not None:
                player.stdin.close()
            player.wait(timeout=120)

            return {
                "success": True,
                "provider": "edge",
                "rate": rate,
                "streamed_to_output": True
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"edge streaming failed: {str(e)}"
            }
        finally:
            if file_handle is not None:
                file_handle.close()
            if player is not None and player.poll() is None:
                player.kill()

    def play_audio_file(self, audio_path: str) -> dict:
        """Play audio file on default system output via ffplay."""
        ffplay_path = shutil.which("ffplay")
        if not ffplay_path:
            return {
                "success": False,
                "error": "ffplay is not installed or not in PATH. Install FFmpeg to enable playback."
            }

        try:
            subprocess.run(
                [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "error", audio_path],
                check=True
            )
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": f"playback failed: {str(e)}"}
    
    async def process_tts_request(self, request_data):
        """Process a TTS request and return a JSON response."""
        try:
            text = request_data.get('text', '')
            play_audio = parse_bool(request_data.get('play_audio'), False)
            stream_playback = parse_bool(request_data.get('stream_playback'), False)

            default_voice = os.environ.get('TTS_VOICE', 'af_heart')
            voice = request_data.get('voice', default_voice)
            speed = float(request_data.get('speed', os.environ.get('TTS_SPEED', 1.0)))
            requested_provider = (request_data.get('provider') or os.environ.get('TTS_PROVIDER', 'auto')).strip().lower()
            lang = request_data.get('lang', os.environ.get('TTS_LANGUAGE', 'en-us'))

            provider = requested_provider
            if provider == "auto":
                if TTS_AVAILABLE:
                    provider = "kokoro"
                elif EDGE_TTS_AVAILABLE:
                    provider = "edge"
                else:
                    provider = "none"

            if provider == "edge" and request_data.get('voice') is None:
                voice = os.environ.get("TTS_EDGE_VOICE", "en-US-AvaNeural")
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
            print(f"Using provider: {provider}, voice: {voice}, speed: {speed}, language: {lang}, play_audio: {play_audio}, stream_playback: {stream_playback}")
            print(f"Output file: {mp3_path}")

            if provider == "none":
                return {
                    "success": False,
                    "error": "No TTS provider is available (kokoro and edge-tts are both unavailable)."
                }
            if provider == "edge" and not EDGE_TTS_AVAILABLE:
                return {
                    "success": False,
                    "error": "Provider 'edge' requested, but edge-tts is not installed."
                }
            if provider == "kokoro" and (not TTS_AVAILABLE or tts_service is None):
                return {
                    "success": False,
                    "error": "Provider 'kokoro' requested, but kokoro service is not available."
                }

            if provider == "edge":
                if play_audio and stream_playback:
                    result = await self.stream_edge_tts_to_player(
                        text=text,
                        voice=voice,
                        speed=speed,
                        output_path=mp3_path
                    )
                else:
                    result = await self.generate_with_edge_tts(
                        text=text,
                        voice=voice,
                        speed=speed,
                        output_path=mp3_path
                    )
            else:
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

            if isinstance(result, dict) and not result.get('success', True):
                print(f"TTS service returned an error: {result}")
                return {
                    "success": False,
                    "error": result.get('error', 'Unknown TTS generation error'),
                    "tts_result": result,
                    "request_params": {
                        "text": text,
                        "provider": provider,
                        "voice": voice,
                        "speed": speed,
                        "lang": lang,
                        "filename": filename
                    },
                    "timestamp": datetime.datetime.now().isoformat()
                }
            
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
                "provider": provider,
                "lang": lang,
                "play_audio": play_audio,
                "stream_playback": stream_playback,
                "s3_uploaded": False
            }

            # For non-streaming mode (or non-edge providers), play after generation.
            if play_audio and not (provider == "edge" and stream_playback):
                playback = self.play_audio_file(mp3_path)
                response_data["played"] = playback.get("success", False)
                if not playback.get("success", False):
                    response_data["playback_error"] = playback.get("error")
            
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
    async def text_to_speech(text: str,
                            provider: str = os.environ.get('TTS_PROVIDER', 'auto'),
                            voice: str = os.environ.get('TTS_VOICE', 'af_heart'),
                            speed: float = float(os.environ.get('TTS_SPEED', 1.0)),
                            lang: str = os.environ.get('TTS_LANGUAGE', 'en-us'),
                            filename: str = None,
                            play_audio: bool = False,
                            stream_playback: bool = False,
                            upload_to_s3: bool = os.environ.get('S3_ENABLED', 'true').lower() == 'true') -> dict:
        """
        Convert text to speech using the Kokoro TTS service.
        
        Args:
            text: The text to convert to speech
            provider: TTS provider (auto, edge, kokoro). Default: auto
            voice: Voice ID to use (default: af_heart)
            speed: Speech speed (default: 1.0)
            lang: Language code (default: en-us)
            filename: Optional filename for the MP3 (default: auto-generated UUID)
            play_audio: Play generated speech on default system output device
            stream_playback: For edge provider, stream chunks directly to output while generating
            upload_to_s3: Whether to upload to S3 if enabled (default: True)
            
        Returns:
            A dictionary with information about the generated audio file
        """
        request_data = {
            "text": text,
            "provider": provider,
            "voice": voice,
            "speed": speed,
            "lang": lang,
            "filename": filename,
            "play_audio": play_audio,
            "stream_playback": stream_playback,
            "upload_to_s3": upload_to_s3
        }
        
        return await mcp_tts_server.process_tts_request(request_data)

    @mcp.tool()
    async def speak_live(text: str,
                        preset: str = "auto",
                        provider: str = "edge",
                        voice: str = None,
                        speed: float = None,
                        lang: str = "auto",
                        filename: str = None) -> dict:
        """
        Speak text immediately to default audio output (headphones/speakers).
        Optimized for live dialogue with minimal parameters.
        """
        resolved_preset, self_check_reset = mcp_tts_server.resolve_live_preset(preset, text)
        request_data = {
            "text": text,
            "provider": provider,
            "voice": voice,
            "speed": speed,
            "lang": lang,
            "preset": resolved_preset,
            "filename": filename,
            "play_audio": True,
            "stream_playback": True,
            "upload_to_s3": False
        }

        result = await mcp_tts_server.process_tts_request(request_data)
        if isinstance(result, dict):
            result["resolved_preset"] = resolved_preset
            result["self_check_reset"] = self_check_reset
            result["live_profile_default"] = mcp_tts_server.live_profile_default
        return result

    @mcp.tool()
    async def speak_live(text: str,
                        preset: str = "auto",
                        provider: str = "edge",
                        voice: str = None,
                        speed: float = None,
                        lang: str = "auto",
                        filename: str = None) -> dict:
        """
        Speak text immediately to default audio output (headphones/speakers).
        Optimized for live dialogue with minimal parameters.
        """
        resolved_preset, self_check_reset = mcp_tts_server.resolve_live_preset(preset, text)
        request_data = {
            "text": text,
            "provider": provider,
            "voice": voice,
            "speed": speed,
            "lang": lang,
            "preset": resolved_preset,
            "filename": filename,
            "play_audio": True,
            "stream_playback": True,
            "upload_to_s3": False
        }

        result = await mcp_tts_server.process_tts_request(request_data)
        if isinstance(result, dict):
            result["resolved_preset"] = resolved_preset
            result["self_check_reset"] = self_check_reset
            result["live_profile_default"] = mcp_tts_server.live_profile_default
        return result

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