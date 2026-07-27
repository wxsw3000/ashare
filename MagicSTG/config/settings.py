# -*- coding: utf-8 -*-
"""
MagicSTG Unified Configuration & Settings Center
Handles environment variables (.env), SSL CA resolution, paths, and YAML config loading.
"""

import os
import yaml
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Base Project Paths
MAGICSTG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)

# Load .env file
ENV_PATHS = [
    os.path.join(MAGICSTG_DIR, 'dbconfig', '.env'),
    os.path.join(PROJECT_ROOT, '.env'),
    os.path.join(MAGICSTG_DIR, '.env'),
]

ENV_LOADED_FROM: Optional[str] = None
for env_path in ENV_PATHS:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        ENV_LOADED_FROM = env_path
        break

# Database Connection Credentials from Environment
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT") or 4000)
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "asharedb")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")


def get_ssl_ca_path() -> Optional[str]:
    """Resolves the absolute path to the SSL CA certificate file."""
    # Check if running on Render
    if os.environ.get('PORT') is not None and os.path.exists("/etc/ssl/cert.pem"):
        return "/etc/ssl/cert.pem"

    if not DB_SSL_CA:
        filename = "isrgrootx1.pem"
    else:
        filename = os.path.basename(DB_SSL_CA)

    candidates = [
        DB_SSL_CA,
        os.path.join(MAGICSTG_DIR, 'dbconfig', filename),
        os.path.join(MAGICSTG_DIR, filename),
        os.path.join(PROJECT_ROOT, filename),
    ]

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.abspath(candidate)

    return None


def load_yaml_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Loads default or specified YAML configuration file."""
    if not config_path:
        config_path = os.path.join(MAGICSTG_DIR, 'config.yaml')

    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}
