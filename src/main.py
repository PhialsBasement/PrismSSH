"""Main application entry point for PrismSSH."""

import sys
import os
import webview
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Handle imports - try relative first, then absolute
try:
    from .config import Config
    from .logger import Logger
    from .api import PrismSSHAPI
except ImportError:
    # Fallback to absolute imports when running as script
    from config import Config
    from logger import Logger
    from api import PrismSSHAPI


def load_html_template() -> str:
    """Load the HTML template and embed CSS/JS."""
    template_path = Path(__file__).parent / "ui" / "template.html"
    css_path = Path(__file__).parent / "ui" / "static" / "styles.css"
    js_path = Path(__file__).parent / "ui" / "static" / "app.js"
    
    if not template_path.exists():
        # Fallback to inline HTML if template file doesn't exist
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>PrismSSH - Template Not Found</title>
            <style>
                body { 
                    font-family: Arial, sans-serif; 
                    text-align: center; 
                    padding: 50px; 
                    background: #1a1a1a; 
                    color: #fff; 
                }
            </style>
        </head>
        <body>
            <h1>PrismSSH</h1>
            <p>Template file not found. Please ensure the UI template is available.</p>
        </body>
        </html>
        """
    
    try:
        # Load template
        with open(template_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # Load CSS
        css_content = ""
        if css_path.exists():
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
        
        # Load JavaScript
        js_content = ""
        if js_path.exists():
            with open(js_path, 'r', encoding='utf-8') as f:
                js_content = f.read()
        
        # Embed CSS and JS into the HTML
        html_content = html_content.replace(
            '    <style>',
            f'    <style>\n{css_content}\n        /*'
        )
        html_content = html_content.replace(
            '    <script>',
            f'    <script>\n{js_content}\n        //'
        )
        
        return html_content
        
    except Exception as e:
        print(f"Error loading template: {e}")
        return f"<html><body><h1>Error loading template: {e}</h1></body></html>"


def main():
    """Main application entry point."""
    print("PrismSSH Starting...")
    
    # Initialize configuration
    config = Config()
    
    # Setup logging
    logger_instance = Logger(config.log_file)
    logger = Logger.get_logger(__name__)
    
    logger.info("=== PrismSSH Starting ===")
    logger.info(f"Config directory: {config.config_dir}")
    logger.info(f"Encryption available: {os.path.exists(config.key_file) if hasattr(config, 'key_file') else 'Unknown'}")
    
    # Ensure config directory exists
    if not config.ensure_config_dir():
        logger.error("Failed to create configuration directory")
        sys.exit(1)
    
    # Check if connections file exists
    if config.connections_file.exists():
        logger.info(f"Found existing connections file: {config.connections_file}")
        try:
            import json
            with open(config.connections_file, 'r') as f:
                data = json.load(f)
                logger.info(f"Loaded {len(data)} saved connections")
        except Exception as e:
            logger.error(f"Error reading connections file: {e}")
    else:
        logger.info("No existing connections file found")
    
    # Create API instance
    try:
        api = PrismSSHAPI(config)
        logger.info("API instance created successfully")
    except Exception as e:
        logger.error(f"Failed to create API instance: {e}")
        sys.exit(1)
    
    # Load HTML template
    html_content = load_html_template()
    
    # Create window
    try:
        window = webview.create_window(
            title=config.get_app_title(),
            html=html_content,
            js_api=api,
            width=config.window_width,
            height=config.window_height,
            min_size=(config.window_min_width, config.window_min_height)
        )
        logger.info("WebView window created")
        
        # Register cleanup on window close
        def on_window_closed():
            logger.info("Window closed, cleaning up...")
            api.cleanup()
        
        # Start the GUI
        logger.info("Starting WebView...")
        webview.start(debug=False)
        
    except Exception as e:
        logger.error(f"Error starting application: {e}")
        sys.exit(1)
    finally:
        # Cleanup
        try:
            api.cleanup()
        except:
            pass
        logger.info("=== PrismSSH Shutdown ===")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)