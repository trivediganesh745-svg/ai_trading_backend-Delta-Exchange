import os
from dotenv import load_dotenv

load_dotenv()

class ProductionConfig:
    """Production configuration"""
    DEBUG = False
    TESTING = False
    
    # API Configuration
    DELTA_BASE_URL = os.getenv('DELTA_BASE_URL', 'https://api.india.delta.exchange')
    DELTA_WS_URL = os.getenv('DELTA_WS_URL', 'wss://socket.india.delta.exchange')
    DELTA_API_KEY = os.getenv('DELTA_API_KEY')
    DELTA_API_SECRET = os.getenv('DELTA_API_SECRET')
    
    # Trading Configuration
    MAX_POSITION_SIZE = int(os.getenv('MAX_POSITION_SIZE', '10'))
    MAX_DAILY_LOSS = float(os.getenv('MAX_DAILY_LOSS', '1000.0'))
    RISK_MANAGEMENT_ENABLED = os.getenv('RISK_MANAGEMENT_ENABLED', 'true').lower() == 'true'

class TestnetConfig(ProductionConfig):
    """Testnet configuration"""
    DELTA_BASE_URL = 'https://cdn-ind.testnet.deltaex.org'
    DELTA_WS_URL = 'wss://socket-ind.testnet.deltaex.org'
    DEBUG = True

class DevelopmentConfig(ProductionConfig):
    """Development configuration"""
    DEBUG = True
    TESTING = False

# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'testnet': TestnetConfig,
    'production': ProductionConfig,
    'default': ProductionConfig
}
