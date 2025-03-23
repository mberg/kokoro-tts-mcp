import soundfile as sf
from kokoro_onnx import Kokoro
import re
import os
import subprocess

class KokoroTTSService:
    def __init__(self, model_path="kokoro-v1.0.onnx", voices_path="voices-v1.0.bin"):
        """Initialize the Kokoro TTS service."""
        try:
            self.kokoro = Kokoro(model_path, voices_path)
            self.available = True
        except Exception as e:
            print(f"Error initializing Kokoro TTS: {e}")
            self.available = False
            
    def get_voices(self):
        """Return a list of available voices."""
        if self.available:
            return self.kokoro.get_voices()
        else:
            # Return default voices if Kokoro is not available
            return ["af_heart", "en_us_male", "en_us_female"]
    
    def generate_audio(self, text, voice="af_heart", speed=1.0, lang="en-us", 
                     output_file="audio.mp3", output_dir="mp3"):
        """Generate audio from text using Kokoro TTS."""
        # Clean up the text (remove markdown links)
        text = self._remove_markdown_links(text)
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        # Ensure output filename has the correct extension
        if not output_file.lower().endswith('.mp3'):
            output_file = f"{os.path.splitext(output_file)[0]}.mp3"
        
        if not self.available:
            # Use fallback TTS method
            return self._generate_with_fallback(text, output_file, output_dir)
        
        try:
            # Create full file paths
            base_filename = os.path.splitext(output_file)[0]
            wav_file = os.path.join(output_dir, f"{base_filename}.wav")
            mp3_file = os.path.join(output_dir, output_file)
            
            # Generate audio
            samples, sample_rate = self.kokoro.create(
                text, voice=voice, speed=speed, lang=lang
            )
            
            # Write audio to WAV file first
            sf.write(wav_file, samples, sample_rate)
            
            # Convert WAV to MP3
            success = self._convert_wav_to_mp3(wav_file, mp3_file)
            
            # Clean up the WAV file
            if success and os.path.exists(wav_file):
                os.remove(wav_file)
                
            return {
                "success": success,
                "mp3_file": mp3_file if success else None
            }
            
        except Exception as e:
            print(f"Error generating audio with Kokoro: {e}")
            return self._generate_with_fallback(text, output_file, output_dir)
    
    def _generate_with_fallback(self, text, output_file, output_dir):
        """Use system TTS as a fallback method."""
        try:
            # Create full file paths
            base_filename = os.path.splitext(output_file)[0]
            wav_file = os.path.join(output_dir, f"{base_filename}.wav")
            mp3_file = os.path.join(output_dir, output_file)
            
            # Use macOS 'say' command or other system TTS
            cmd = ['say', '-o', wav_file, text]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            # Convert to MP3
            success = self._convert_wav_to_mp3(wav_file, mp3_file)
            
            # Clean up WAV file
            if success and os.path.exists(wav_file):
                os.remove(wav_file)
                
            return {
                "success": success,
                "mp3_file": mp3_file if success else None
            }
            
        except Exception as e:
            print(f"Error with fallback TTS: {e}")
            return {
                "success": False,
                "mp3_file": None
            }
    
    def _remove_markdown_links(self, text):
        """Remove markdown links from text."""
        # Remove inline links like [text](url)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        # Remove reference-style links like [text][ref]
        text = re.sub(r'\[([^\]]+)\]\[[^\]]*\]', r'\1', text)
        # Remove reference link definitions like [ref]: url
        text = re.sub(r'^\s*\[[^\]]+\]:\s*.*$', '', text, flags=re.MULTILINE)
        return text
        
    def _convert_wav_to_mp3(self, wav_file, mp3_file):
        """Convert WAV file to MP3 using ffmpeg."""
        try:
            cmd = ['ffmpeg', '-y', '-i', wav_file, '-codec:a', 'libmp3lame', '-qscale:a', '2', mp3_file]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except Exception as e:
            print(f"Error converting to MP3: {e}")
            return False 