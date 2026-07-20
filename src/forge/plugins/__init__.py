"""Extensible plugin system for Forge."""

import os
import sys
import importlib
import importlib.util
from pathlib import Path
from typing import Dict, List, Any, Callable, Optional
from dataclasses import dataclass
import json
import inspect
from abc import ABC, abstractmethod


class PluginManifest:
    """Represents the manifest file for a plugin."""
    
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = self._load_manifest()
    
    def _load_manifest(self) -> Dict[str, Any]:
        """Load plugin manifest from JSON file."""
        if not self.path.exists():
            raise FileNotFoundError(f"Plugin manifest not found: {self.path}")
        
        with open(self.path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        required_fields = ['name', 'version', 'description']
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field '{field}' in plugin manifest")
        
        return data
    
    @property
    def name(self) -> str:
        return self.data['name']
    
    @property
    def version(self) -> str:
        return self.data['version']
    
    @property
    def description(self) -> str:
        return self.data['description']
    
    @property
    def author(self) -> str:
        return self.data.get('author', 'Unknown')
    
    @property
    def entry_point(self) -> str:
        return self.data.get('entry_point', 'plugin:register')
    
    @property
    def dependencies(self) -> List[str]:
        return self.data.get('dependencies', [])


class PluginInterface(ABC):
    """Base interface that all plugins must implement."""
    
    @abstractmethod
    def get_name(self) -> str:
        """Get the plugin name."""
        pass
    
    @abstractmethod
    def get_version(self) -> str:
        """Get the plugin version."""
        pass
    
    @abstractmethod
    def initialize(self, forge_app: Any) -> bool:
        """Initialize the plugin with the Forge app instance."""
        pass
    
    @abstractmethod
    def shutdown(self) -> bool:
        """Shutdown the plugin."""
        pass


@dataclass
class ToolRegistration:
    """Represents a tool that can be registered by a plugin."""
    name: str
    description: str
    function: Callable
    parameters: Dict[str, Any] = None


@dataclass
class AgentRegistration:
    """Represents an agent that can be registered by a plugin."""
    name: str
    description: str
    cls: type


@dataclass
class CommandRegistration:
    """Represents a command that can be registered by a plugin."""
    name: str
    description: str
    function: Callable
    options: List[Dict[str, Any]] = None


class PluginValidationError(Exception):
    """Raised when a plugin fails validation."""
    pass


class PluginLoader:
    """Loads and validates plugins."""
    
    def __init__(self, plugin_dirs: List[str] = None):
        self.plugin_dirs = plugin_dirs or [str(Path.home() / ".forge" / "plugins")]
        self.loaded_plugins = {}
        self._validate_plugin_dirs()
    
    def _validate_plugin_dirs(self):
        """Validate that plugin directories exist and are secure."""
        for plugin_dir in self.plugin_dirs:
            dir_path = Path(plugin_dir)
            if not dir_path.exists():
                dir_path.mkdir(parents=True, exist_ok=True)
    
    def find_plugins(self) -> List[Path]:
        """Find all plugin manifest files."""
        plugins = []
        for plugin_dir in self.plugin_dirs:
            dir_path = Path(plugin_dir)
            if not dir_path.exists():
                continue
            
            for manifest_path in dir_path.rglob("plugin.json"):
                plugins.append(manifest_path)
        
        return plugins
    
    def load_plugin(self, manifest_path: Path) -> Optional[PluginInterface]:
        """Load a single plugin from manifest path."""
        try:
            manifest = PluginManifest(manifest_path)
            
            if not self._validate_plugin_security(manifest_path):
                raise PluginValidationError(f"Plugin security validation failed: {manifest_path}")
            
            plugin_dir = manifest_path.parent
            entry_point = manifest.entry_point
            module_name, attr_name = entry_point.split(':')
            
            sys.path.insert(0, str(plugin_dir))
            try:
                spec = importlib.util.spec_from_file_location(
                    f"forge_plugin_{manifest.name}",
                    plugin_dir / f"{module_name}.py"
                )
                if spec is None:
                    raise ImportError(f"Could not load plugin spec: {module_name}")
                
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                plugin_obj = getattr(module, attr_name)
                
                if inspect.isclass(plugin_obj):
                    plugin_instance = plugin_obj()
                else:
                    plugin_instance = plugin_obj()
                
                if not isinstance(plugin_instance, PluginInterface):
                    raise PluginValidationError(
                        f"Plugin does not implement PluginInterface: {manifest.name}"
                    )
                
                self.loaded_plugins[manifest.name] = {
                    'instance': plugin_instance,
                    'manifest': manifest,
                    'path': plugin_dir
                }
                
                return plugin_instance
                
            finally:
                if str(plugin_dir) in sys.path:
                    sys.path.remove(str(plugin_dir))
                    
        except Exception as e:
            print(f"Error loading plugin from {manifest_path}: {e}")
            return None
    
    def _validate_plugin_security(self, manifest_path: Path) -> bool:
        """Perform basic security validation on plugin."""
        plugin_dir = manifest_path.parent
        
        dangerous_paths = [
            "/etc", "/root", "/proc", "/sys", "/dev",
        ]
        
        plugin_realpath = str(plugin_dir.resolve())
        for dangerous in dangerous_paths:
            if plugin_realpath.startswith(dangerous):
                return False
        
        return True
    
    def load_all_plugins(self) -> List[PluginInterface]:
        """Load all available plugins."""
        plugin_instances = []
        
        for manifest_path in self.find_plugins():
            plugin = self.load_plugin(manifest_path)
            if plugin:
                plugin_instances.append(plugin)
        
        return plugin_instances


class PluginManager:
    """Manages the plugin system and registered extensions."""
    
    def __init__(self):
        self.plugins: Dict[str, PluginInterface] = {}
        self.tools: Dict[str, ToolRegistration] = {}
        self.agents: Dict[str, AgentRegistration] = {}
        self.commands: Dict[str, CommandRegistration] = {}
        self.themes: Dict[str, Dict[str, Any]] = {}
        self.templates: Dict[str, str] = {}
        self.loader = PluginLoader()
    
    def register_plugin(self, plugin: PluginInterface) -> bool:
        """Register a plugin instance."""
        try:
            name = plugin.get_name()
            if name in self.plugins:
                print(f"Plugin {name} is already registered")
                return False
            
            self.plugins[name] = plugin
            return True
        except Exception as e:
            print(f"Error registering plugin {plugin.get_name()}: {e}")
            return False
    
    def initialize_all_plugins(self, forge_app: Any) -> Dict[str, bool]:
        """Initialize all registered plugins."""
        results = {}
        for name, plugin in self.plugins.items():
            try:
                success = plugin.initialize(forge_app)
                results[name] = success
                if not success:
                    print(f"Plugin {name} failed to initialize")
            except Exception as e:
                print(f"Error initializing plugin {name}: {e}")
                results[name] = False
        
        return results
    
    def shutdown_all_plugins(self) -> Dict[str, bool]:
        """Shutdown all registered plugins."""
        results = {}
        for name, plugin in self.plugins.items():
            try:
                success = plugin.shutdown()
                results[name] = success
            except Exception as e:
                print(f"Error shutting down plugin {name}: {e}")
                results[name] = False
        
        return results
    
    def register_tool(self, tool: ToolRegistration) -> bool:
        """Register a tool from a plugin."""
        if tool.name in self.tools:
            print(f"Tool {tool.name} is already registered")
            return False
        
        self.tools[tool.name] = tool
        return True
    
    def register_agent(self, agent: AgentRegistration) -> bool:
        """Register an agent from a plugin."""
        if agent.name in self.agents:
            print(f"Agent {agent.name} is already registered")
            return False
        
        self.agents[agent.name] = agent
        return True
    
    def register_command(self, command: CommandRegistration) -> bool:
        """Register a command from a plugin."""
        if command.name in self.commands:
            print(f"Command {command.name} is already registered")
            return False
        
        self.commands[command.name] = command
        return True
    
    def register_theme(self, name: str, theme_data: Dict[str, Any]) -> bool:
        """Register a theme from a plugin."""
        if name in self.themes:
            print(f"Theme {name} is already registered")
            return False
        
        self.themes[name] = theme_data
        return True
    
    def register_template(self, name: str, template_path: str) -> bool:
        """Register a template from a plugin."""
        if name in self.templates:
            print(f"Template {name} is already registered")
            return False
        
        self.templates[name] = template_path
        return True
    
    def get_all_tools(self) -> List[ToolRegistration]:
        """Get all registered tools."""
        return list(self.tools.values())
    
    def get_all_agents(self) -> List[AgentRegistration]:
        """Get all registered agents."""
        return list(self.agents.values())
    
    def get_all_commands(self) -> List[CommandRegistration]:
        """Get all registered commands."""
        return list(self.commands.values())
    
    def get_all_themes(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered themes."""
        return self.themes.copy()
    
    def get_all_templates(self) -> Dict[str, str]:
        """Get all registered templates."""
        return self.templates.copy()
    
    def load_plugins_from_dirs(self, plugin_dirs: List[str]) -> List[str]:
        """Load plugins from specified directories."""
        self.loader = PluginLoader(plugin_dirs)
        loaded_plugins = self.loader.load_all_plugins()
        
        results = []
        for plugin in loaded_plugins:
            if self.register_plugin(plugin):
                results.append(plugin.get_name())
        
        return results


class BasePlugin(PluginInterface):
    """Base class for creating plugins."""
    
    def __init__(self, name: str, version: str, description: str):
        self._name = name
        self._version = version
        self._description = description
        self._initialized = False
    
    def get_name(self) -> str:
        return self._name
    
    def get_version(self) -> str:
        return self._version
    
    def get_description(self) -> str:
        return self._description
    
    def initialize(self, forge_app: Any) -> bool:
        """Override this method to perform plugin initialization."""
        self._initialized = True
        return True
    
    def shutdown(self) -> bool:
        """Override this method to perform plugin cleanup."""
        self._initialized = False
        return True
    
    def is_initialized(self) -> bool:
        return self._initialized


def register_plugin_command(cli_app, plugin_manager: PluginManager):
    """Register plugin-related commands with the CLI app."""
    import click
    
    @cli_app.command()
    @click.argument('action', type=click.Choice(['list', 'install', 'uninstall']))
    @click.argument('plugin_name', required=False)
    def plugins(action, plugin_name):
        """Manage plugins."""
        if action == 'list':
            click.echo("Registered plugins:")
            for name, plugin in plugin_manager.plugins.items():
                status = "✓" if plugin.is_initialized() else "?"
                click.echo(f"  {status} {name} ({plugin.get_version()}) - {plugin.get_description()}")
            
            if plugin_manager.tools:
                click.echo("\nAvailable tools from plugins:")
                for name in plugin_manager.tools.keys():
                    click.echo(f"  • {name}")
        
        elif action == 'install':
            if not plugin_name:
                click.echo("Plugin name required for install", err=True)
                return 1
            click.echo(f"Plugin installation would happen here: {plugin_name}")
        
        elif action == 'uninstall':
            if not plugin_name:
                click.echo("Plugin name required for uninstall", err=True)
                return 1
            click.echo(f"Plugin uninstallation would happen here: {plugin_name}")


_plugin_manager = None


def get_plugin_manager() -> PluginManager:
    """Get the global plugin manager instance."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager
