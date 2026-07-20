"""Multi-language support for Forge."""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from abc import ABC, abstractmethod
import subprocess
import json


@dataclass
class LanguageInfo:
    """Information about a programming language."""
    name: str
    display_name: str
    file_extensions: List[str]
    project_files: List[str]


class LanguageAdapter(ABC):
    """Abstract base class for language-specific adapters."""
    
    @abstractmethod
    def get_project_type(self, project_path: str) -> Optional[str]:
        pass
    
    @abstractmethod
    def get_dependencies(self, project_path: str) -> List[str]:
        pass
    
    @abstractmethod
    def run_tests(self, project_path: str) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    def run_lint(self, project_path: str) -> Dict[str, Any]:
        pass


class PythonLanguageAdapter(LanguageAdapter):
    """Language adapter for Python projects."""
    
    def get_project_type(self, project_path: str) -> Optional[str]:
        project_path = Path(project_path)
        if (project_path / "setup.py").exists():
            return "setup.py"
        elif (project_path / "pyproject.toml").exists():
            return "pyproject-toml"
        elif (project_path / "requirements.txt").exists():
            return "requirements-txt"
        return None
    
    def get_dependencies(self, project_path: str) -> List[str]:
        project_path = Path(project_path)
        dependencies = []
        req_file = project_path / "requirements.txt"
        if req_file.exists():
            with open(req_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        dependencies.append(line)
        return dependencies
    
    def run_tests(self, project_path: str) -> Dict[str, Any]:
        try:
            result = subprocess.run(["python", "-m", "pytest", str(project_path)], capture_output=True, text=True, timeout=300)
            return {"command": "pytest", "returncode": result.returncode, "success": result.returncode == 0}
        except FileNotFoundError:
            return {"success": False, "error": "pytest not found"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "tests timed out"}
    
    def run_lint(self, project_path: str) -> Dict[str, Any]:
        try:
            result = subprocess.run(["flake8", str(project_path)], capture_output=True, text=True, timeout=120)
            return {"flake8": {"success": result.returncode == 0}}
        except FileNotFoundError:
            return {"flake8": {"success": False, "error": "flake8 not found"}}


class TypeScriptLanguageAdapter(LanguageAdapter):
    """Language adapter for TypeScript/JavaScript projects."""
    
    def get_project_type(self, project_path: str) -> Optional[str]:
        project_path = Path(project_path)
        if (project_path / "package.json").exists():
            return "npm"
        elif (project_path / "tsconfig.json").exists():
            return "typescript"
        return None
    
    def get_dependencies(self, project_path: str) -> List[str]:
        project_path = Path(project_path)
        pkg_file = project_path / "package.json"
        if pkg_file.exists():
            with open(pkg_file, 'r') as f:
                data = json.load(f)
                deps = list(data.get("dependencies", {}).keys())
                deps.extend(data.get("devDependencies", {}).keys())
                return deps
        return []
    
    def run_tests(self, project_path: str) -> Dict[str, Any]:
        try:
            result = subprocess.run(["npm", "test"], cwd=str(project_path), capture_output=True, text=True, timeout=300)
            return {"command": "npm test", "returncode": result.returncode, "success": result.returncode == 0}
        except FileNotFoundError:
            return {"success": False, "error": "npm not found"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "tests timed out"}
    
    def run_lint(self, project_path: str) -> Dict[str, Any]:
        return {"eslint": {"success": True}}


class LanguageDetector:
    """Detects programming languages in a project."""
    
    def __init__(self):
        self.language_adapters = {
            "python": PythonLanguageAdapter(),
            "typescript": TypeScriptLanguageAdapter(),
            "javascript": TypeScriptLanguageAdapter(),
        }
        
        self.language_patterns = {
            "python": {"extensions": [".py"], "files": ["requirements.txt", "setup.py", "pyproject.toml"]},
            "typescript": {"extensions": [".ts", ".tsx"], "files": ["tsconfig.json", "package.json"]},
            "javascript": {"extensions": [".js", ".jsx"], "files": ["package.json"]}
        }
    
    def detect_languages(self, project_path: str) -> List[Tuple[str, float]]:
        project_path = Path(project_path)
        language_scores = {}
        
        for root, dirs, files in os.walk(project_path):
            for file in files:
                file_ext = Path(file).suffix.lower()
                for lang, patterns in self.language_patterns.items():
                    if file_ext in patterns["extensions"]:
                        language_scores[lang] = language_scores.get(lang, 0) + 1
        
        for lang, patterns in self.language_patterns.items():
            for project_file in patterns["files"]:
                if (project_path / project_file).exists():
                    language_scores[lang] = language_scores.get(lang, 0) + 10
        
        sorted_langs = sorted(language_scores.items(), key=lambda x: x[1], reverse=True)
        if sorted_langs:
            max_score = sorted_langs[0][1]
            if max_score > 0:
                return [(lang, score/max_score) for lang, score in sorted_langs]
        return []
    
    def get_adapter(self, language: str) -> Optional[LanguageAdapter]:
        return self.language_adapters.get(language.lower())


class MultiLanguageManager:
    """Manages multi-language support in Forge."""
    
    def __init__(self):
        self.detector = LanguageDetector()
        self.adapters = self.detector.language_adapters
    
    def analyze_project(self, project_path: str) -> Dict[str, Any]:
        languages = self.detector.detect_languages(project_path)
        result = {
            "detected_languages": languages,
            "primary_language": languages[0][0] if languages else None,
            "language_details": {}
        }
        for lang, confidence in languages:
            adapter = self.detector.get_adapter(lang)
            if adapter:
                try:
                    project_type = adapter.get_project_type(project_path)
                    dependencies = adapter.get_dependencies(project_path)
                    result["language_details"][lang] = {
                        "confidence": confidence,
                        "project_type": project_type,
                        "dependencies": dependencies
                    }
                except Exception as e:
                    result["language_details"][lang] = {"confidence": confidence, "error": str(e)}
        return result
    
    def get_supported_languages(self) -> List[str]:
        return list(self.adapters.keys())


def get_multi_language_manager() -> MultiLanguageManager:
    """Get the global multi-language manager instance."""
    if not hasattr(get_multi_language_manager, '_instance'):
        get_multi_language_manager._instance = MultiLanguageManager()
    return get_multi_language_manager._instance


def register_language_commands(cli_app):
    """Register language-related commands with the CLI app."""
    import click
    
    @cli_app.command()
    @click.argument('project_path', type=click.Path(exists=True))
    def detect(project_path):
        """Detect languages in a project."""
        manager = get_multi_language_manager()
        result = manager.analyze_project(project_path)
        click.echo(f"Detected languages in {project_path}:")
        for lang, confidence in result["detected_languages"]:
            click.echo(f"  {lang}: {confidence:.2f}")
        if result["primary_language"]:
            click.echo(f"\nPrimary language: {result['primary_language']}")
