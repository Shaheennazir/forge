"""Input validation and sanitization for Forge."""

import os
import re
from pathlib import Path
from typing import Union, List, Dict, Any
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of input validation."""
    is_valid: bool
    sanitized_value: Union[str, List, Dict] = None
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []


class InputValidator:
    """Validates and sanitizes user inputs to prevent security issues."""
    
    def __init__(self):
        self.dangerous_patterns = [
            r"(?:\.\./)+",  # Directory traversal
            r"rm\s+-rf",  # Dangerous rm commands
            r";\s*rm",  # Command chaining with rm
        ]
    
    def validate_path(self, path: str, base_path: str = None) -> ValidationResult:
        """Validate and sanitize file paths to prevent directory traversal."""
        result = ValidationResult(is_valid=True)
        
        if not path:
            result.is_valid = False
            result.errors.append("Path cannot be empty")
            return result
        
        try:
            path_obj = Path(path).resolve()
            
            if base_path:
                base_path_obj = Path(base_path).resolve()
                try:
                    path_obj.relative_to(base_path_obj)
                except ValueError:
                    result.is_valid = False
                    result.errors.append(f"Path '{path}' is outside allowed base path '{base_path}'")
                    return result
            
            result.sanitized_value = str(path_obj)
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"Invalid path format: {str(e)}")
        
        return result
    
    def validate_command(self, command: str, allow_complex: bool = False) -> ValidationResult:
        """Validate shell commands to prevent command injection."""
        result = ValidationResult(is_valid=True)
        
        if not command:
            result.is_valid = False
            result.errors.append("Command cannot be empty")
            return result
        
        for pattern in self.dangerous_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                result.is_valid = False
                result.errors.append(f"Dangerous command pattern detected: {pattern}")
                return result
        
        if not allow_complex:
            forbidden_chars = [';', '|', '&', '`', '$(']
            for char in forbidden_chars:
                if char in command:
                    result.is_valid = False
                    result.errors.append(f"Command contains forbidden character: {char}")
                    return result
        
        result.sanitized_value = command.strip()
        return result
    
    def validate_prompt(self, prompt: str, max_length: int = 10000) -> ValidationResult:
        """Validate user prompts to prevent injection attacks."""
        result = ValidationResult(is_valid=True)
        
        if not prompt:
            result.is_valid = False
            result.errors.append("Prompt cannot be empty")
            return result
        
        if len(prompt) > max_length:
            result.is_valid = False
            result.errors.append(f"Prompt exceeds maximum length of {max_length} characters")
            return result
        
        result.sanitized_value = prompt.strip()
        return result
    
    def validate_tool_parameters(self, tool_name: str, params: Dict[str, Any]) -> ValidationResult:
        """Validate parameters for specific tools."""
        result = ValidationResult(is_valid=True)
        
        if tool_name == "bash":
            if "command" in params:
                cmd_result = self.validate_command(str(params["command"]))
                if not cmd_result.is_valid:
                    result.is_valid = False
                    result.errors.extend(cmd_result.errors)
                if cmd_result.sanitized_value:
                    params["command"] = cmd_result.sanitized_value
        
        elif tool_name in ["read", "write", "search"]:
            for param_name in ["path", "file", "filepath"]:
                if param_name in params:
                    path_result = self.validate_path(str(params[param_name]))
                    if not path_result.is_valid:
                        result.is_valid = False
                        result.errors.extend(path_result.errors)
                    if path_result.sanitized_value:
                        params[param_name] = path_result.sanitized_value
        
        result.sanitized_value = params
        return result


class FileSystemValidator:
    """Validates file system operations to prevent unauthorized access."""
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = workspace_path
    
    def set_workspace(self, workspace_path: str):
        """Set the allowed workspace path."""
        if workspace_path:
            self.workspace_path = Path(workspace_path).resolve()
    
    def validate_file_access(self, file_path: str) -> ValidationResult:
        """Validate that a file path is within allowed boundaries."""
        validator = InputValidator()
        
        if not self.workspace_path:
            return ValidationResult(is_valid=False, errors=["Workspace path not configured"])
        
        return validator.validate_path(file_path, str(self.workspace_path))


def get_validator() -> InputValidator:
    """Get a shared validator instance."""
    if not hasattr(get_validator, '_instance'):
        get_validator._instance = InputValidator()
    return get_validator._instance


def get_filesystem_validator(workspace_path: str = None) -> FileSystemValidator:
    """Get a filesystem validator instance."""
    if not hasattr(get_filesystem_validator, '_instance'):
        get_filesystem_validator._instance = FileSystemValidator(workspace_path)
    return get_filesystem_validator._instance
