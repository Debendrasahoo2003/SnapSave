from flask import Flask, render_template, request, jsonify, send_file, url_for
from flask_cors import CORS
import yt_dlp
import os
import re
import uuid
import threading
import time
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # Allow all origins

# Configuration
app.config['DOWNLOAD_FOLDER'] = 'downloads'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.config['CLEANUP_TIME'] = 3600
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Ensure download folder exists
Path(app.config['DOWNLOAD_FOLDER']).mkdir(exist_ok=True)

# Store download status
downloads = {}

def cleanup_old_files():
    """Remove files older than CLEANUP_TIME seconds"""
    while True:
        time.sleep(300)
        current_time = time.time()
        for filename in os.listdir(app.config['DOWNLOAD_FOLDER']):
            filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > app.config['CLEANUP_TIME']:
                    try:
                        os.remove(filepath)
                        logger.info(f"Deleted old file: {filename}")
                    except Exception as e:
                        logger.error(f"Error deleting {filename}: {e}")

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

def is_valid_url(url):
    """Validate URL format"""
    if not url:
        return False
    # Handle URLs without protocol
    if url.startswith('//'):
        url = 'https:' + url
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return url_pattern.match(url) is not None

def fix_url(url):
    """Fix malformed URLs"""
    url = url.strip()
    if url.startswith('//'):
        url = 'https:' + url
    elif not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def get_video_info(url):
    """Extract video information without downloading"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ignoreerrors': True,
        'no_check_certificate': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Fetching info for URL: {url}")
            info = ydl.extract_info(url, download=False)
            if info:
                # Handle live streams
                duration = info.get('duration', 0)
                if info.get('is_live'):
                    duration = 'LIVE'
                else:
                    duration = duration if duration else 0
                    
                return {
                    'success': True,
                    'title': info.get('title', 'Unknown'),
                    'duration': duration,
                    'thumbnail': info.get('thumbnail', ''),
                    'uploader': info.get('uploader', 'Unknown'),
                    'views': info.get('view_count', 0),
                    'is_live': info.get('is_live', False)
                }
        return {'success': False, 'error': 'Could not fetch video info'}
    except Exception as e:
        logger.error(f"Error fetching video info: {str(e)}")
        return {'success': False, 'error': str(e)}

def download_video(url, video_id):
    """Download video from URL"""
    ydl_opts = {
        'outtmpl': os.path.join(app.config['DOWNLOAD_FOLDER'], f'{video_id}.%(ext)s'),
        'format': 'best[ext=mp4]/best',  # Simplified format selection
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'extract_flat': False,
        'no_check_certificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Downloading video from URL: {url}")
            info = ydl.extract_info(url, download=True)
            
            # Get the actual filename
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                # Try with different extensions
                for ext in ['.mp4', '.webm', '.mkv']:
                    test_file = filename.rsplit('.', 1)[0] + ext
                    if os.path.exists(test_file):
                        filename = test_file
                        break
            
            file_size = os.path.getsize(filename) if os.path.exists(filename) else 0
            
            return {
                'success': True,
                'filename': os.path.basename(filename),
                'title': info.get('title', 'Video'),
                'filesize': file_size
            }
    except Exception as e:
        logger.error(f"Error downloading video: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/api/info', methods=['POST'])
def video_info():
    """Get video information"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data received'}), 400
            
        url = data.get('url', '').strip()
        logger.info(f"Info request for URL: {url}")
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        # Fix URL
        url = fix_url(url)
        
        if not is_valid_url(url):
            return jsonify({'error': 'Invalid URL format'}), 400
        
        result = get_video_info(url)
        if result.get('success'):
            return jsonify({'success': True, 'info': result})
        else:
            return jsonify({'error': result.get('error', 'Unable to fetch video info')}), 400
            
    except Exception as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/download', methods=['POST'])
def download():
    """Download video"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data received'}), 400
            
        url = data.get('url', '').strip()
        logger.info(f"Download request for URL: {url}")
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        # Fix URL
        url = fix_url(url)
        
        if not is_valid_url(url):
            return jsonify({'error': 'Invalid URL format'}), 400
        
        # Generate unique ID for this download
        video_id = str(uuid.uuid4())[:8]
        
        # Start download
        result = download_video(url, video_id)
        
        if result['success']:
            download_url = url_for('download_file', filename=result['filename'], _external=True)
            logger.info(f"Download successful: {result['filename']}")
            return jsonify({
                'success': True,
                'message': 'Video downloaded successfully',
                'download_url': download_url,
                'filename': result['filename'],
                'title': result['title']
            })
        else:
            logger.error(f"Download failed: {result.get('error')}")
            return jsonify({'error': result.get('error', 'Download failed')}), 500
            
    except Exception as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    """Serve the downloaded file"""
    try:
        filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            logger.info(f"Serving file: {filename}")
            return send_file(
                filepath, 
                as_attachment=True, 
                download_name=filename,
                mimetype='video/mp4'
            )
        else:
            logger.error(f"File not found: {filename}")
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        logger.error(f"Error serving file: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Run on all interfaces for better connectivity
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)