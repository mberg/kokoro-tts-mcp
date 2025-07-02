[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/mberg-kokoro-tts-mcp-badge.png)](https://mseep.ai/app/mberg-kokoro-tts-mcp)

## Kokoro Text to Speech (TTS) MCP Server

Kokoro Text to Speech MCP server that generates .mp3 files with option to upload to S3.

Uses: https://huggingface.co/spaces/hexgrad/Kokoro-TTS

## Configuration

* Clone to a local repo.
* Download the [Kokoro Onnx Weights](https://github.com/thewh1teagle/kokoro-onnx) for [kokoro-v1.0.onnx](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx) and [voices-v1.0.bin](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin) and store in the same repo.

Add the following to your MCP configs. Update with your own values.

```
  "kokoro-tts-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/toyourlocal/kokoro-tts-mcp",
        "run",
        "mcp-tts.py"
      ],
      "env": {
        "TTS_VOICE": "af_heart",
        "TTS_SPEED": "1.0",
        "TTS_LANGUAGE": "en-us",
        "AWS_ACCESS_KEY_ID": "",
        "AWS_SECRET_ACCESS_KEY": "",
        "AWS_REGION": "us-east-1",
        "AWS_S3_FOLDER": "mp3",
        "S3_ENABLED": "true",
        "MP3_FOLDER": "/path/to/mp3"
      } 
    }
```

### Install ffmmeg

This is needed to convert .wav to .mp3 files

For mac:

``` 
brew install ffmpeg
```

To run locally add these to your .env file.  See env.example and copy to .env and modify with your own values.

### Supported Environment Variables

- `AWS_ACCESS_KEY_ID`: Your AWS access key ID
- `AWS_SECRET_ACCESS_KEY`: Your AWS secret access key
- `AWS_S3_BUCKET_NAME`: S3 bucket name
- `AWS_S3_REGION`: S3 region (e.g., us-east-1)
- `AWS_S3_FOLDER`: Folder path within the S3 bucket
- `AWS_S3_ENDPOINT_URL`: Optional custom endpoint URL for S3-compatible storage
- `MCP_HOST`: Host to bind the server to (default: 0.0.0.0)
- `MCP_PORT`: Port to listen on (default: 9876)
- `MCP_CLIENT_HOST`: Hostname for client connections to the server (default: localhost)
- `DEBUG`: Enable debug mode (set to "true" or "1")
- `S3_ENABLED`: Enable S3 uploads (set to "true" or "1")
- `MP3_FOLDER`: Path to store MP3 files (default is 'mp3' folder in script directory)
- `MP3_RETENTION_DAYS`: Number of days to keep MP3 files before automatic deletion
- `DELETE_LOCAL_AFTER_S3_UPLOAD`: Whether to delete local MP3 files after successful S3 upload (set to "true" or "1")
- `TTS_VOICE`: Default voice for the TTS client (default: af_heart)
- `TTS_SPEED`: Default speed for the TTS client (default: 1.0)
- `TTS_LANGUAGE`: Default language for the TTS client (default: en-us)

## Running the Server Locally

Preferred method use UV 
```
uv run mcp-tts.py
```


## Using the TTS Client

The `mcp_client.py` script allows you to send TTS requests to the server. It can be used as follows:

### Connection Settings

When running the server and client on the same machine:
- Server should bind to `0.0.0.0` (all interfaces) or `127.0.0.1` (localhost only)
- Client should connect to `localhost` or `127.0.0.1`


### Basic Usage

```bash
python mcp_client.py --text "Hello, world!"
```

### Reading Text from a File

```bash
python mcp_client.py --file my_text.txt
```

### Customizing Voice and Speed

```bash
python mcp_client.py --text "Hello, world!" --voice "en_female" --speed 1.2
```

### Disabling S3 Upload

```bash
python mcp_client.py --text "Hello, world!" --no-s3
```

### Command-line Options

```bash
python mcp_client.py --help
```

## MP3 File Management

The TTS server generates MP3 files that are stored locally and optionally uploaded to S3. You can configure how these files are managed:

### Local Storage

- Set `MP3_FOLDER` in your `.env` file to specify where MP3 files are stored
- Files are kept in this folder unless automatically deleted

### Automatic Cleanup

- Set `MP3_RETENTION_DAYS=30` (or any number) to automatically delete files older than that number of days
- Set `DELETE_LOCAL_AFTER_S3_UPLOAD=true` to delete local files immediately after successful S3 upload

### S3 Integration

- Enable/disable S3 uploads with `S3_ENABLED=true` or `DISABLE_S3=true`
- Configure AWS credentials and bucket settings in the `.env` file
- S3 uploads can be disabled per-request using the client's `--no-s3` option

