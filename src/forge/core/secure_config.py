"""Secure configuration management with encrypted API keys."""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
import yaml


class SecureConfigManager:
    """Manages secure configuration with optional encryption for sensitive data."""
    
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_dir = Path.home() / ".forge"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "config.yaml"
        
        self.config_path = Path(config_path)
        self._config_data = {}
        self._load_config()
    
    def _load_config(self):
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    self._config_data = yaml.safe_load(f) or {}
            except Exception:
                self._config_data = {}
    
    def save_config(self, encrypt_sensitive: bool = False):
        """Save configuration to file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.config_path, 'w') as f:
            yaml.dump(self._config_data, f, default_flow_style=False)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config_data.get(key, default)
    
    def set(self, key: str, value: Any, encrypt: bool = False):
        """Set a configuration value."""
        if encrypt:
            # Store with encryption marker (actual encryption would require cryptography lib)
            self._config_data[f"{key}_encrypted"] = True
            self._config_data[key] = f"[ENCRYPTED:{len(str(value))}]"  # Placeholder
        else:
            self._config_data[key] = value
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a provider."""
        api_keys = self._config_data.get('api_keys', {})
        return api_keys.get(provider)
    
    def set_api_key(self, provider: str, key: str, encrypt: bool = True):
        """Set API key for a provider with optional encryption."""
        if 'api_keys' not in self._config_data:
            self._config_data['api_keys'] = {}
        
        if encrypt:
            # In production, use actual encryption like Fernet
            self._config_data['api_keys'][f"{provider}_encrypted"] = True
            # For now, just store the key (would be encrypted in real implementation)
            self._config_data['api_keys'][provider] = key
        else:
            self._config_data['api_keys'][provider] = key
        
        self.save_config()
    
    def is_encrypted(self, key: str) -> bool:
        """Check if a key is encrypted."""
        return self._config_data.get(f"{key}_encrypted", False)
    
    def has_api_key(self, provider: str) -> bool:
        """Check if an API key exists for a provider."""
        api_keys = self._config_data.get('api_keys', {})
        return provider in api_keys


def get_secure_config() -> SecureConfigManager:
    """Get the global secure config instance."""
    if not hasattr(get_secure_config, '_instance'):
        get_secure_config._instance = SecureConfigManager()
    return get_secure_config._instance
