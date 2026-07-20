"""Project templates for Forge."""

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
import yaml


@dataclass
class TemplateInfo:
    """Information about a project template."""
    name: str
    description: str
    tags: List[str]
    path: str
    version: str = "1.0.0"


class TemplateManager:
    """Manage project templates for Forge."""
    
    def __init__(self, templates_dir: str = None):
        if templates_dir is None:
            templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        
        self.templates_dir = templates_dir
        self._ensure_templates_exist()
    
    def _ensure_templates_exist(self):
        """Ensure the templates directory exists and create default templates."""
        os.makedirs(self.templates_dir, exist_ok=True)
        self._create_python_cli_template()
        self._create_python_library_template()
        self._create_python_service_template()
    
    def _create_python_cli_template(self):
        """Create a Python CLI template."""
        template_dir = os.path.join(self.templates_dir, "python-cli")
        if os.path.exists(template_dir):
            return
        
        os.makedirs(template_dir)
        
        metadata = {
            "name": "python-cli",
            "description": "Python CLI application template",
            "tags": ["python", "cli", "console"],
            "version": "1.0.0"
        }
        
        with open(os.path.join(template_dir, "template.yaml"), "w") as f:
            yaml.dump(metadata, f)
        
        # Create src/package structure
        pkg_dir = os.path.join(template_dir, "src", "{{ package_name }}")
        os.makedirs(pkg_dir, exist_ok=True)
        tests_dir = os.path.join(template_dir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        
        files = {
            os.path.join(template_dir, "README.md"): f"""# {{{{ project_name }}}}

{{{{ project_description }}}}

## Installation

```bash
pip install -e .
```

## Usage

```bash
python -m {{{{ package_name }}}} --help
```
""",
            os.path.join(template_dir, "pyproject.toml"): f"""[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{{{{ project_name }}}}"
version = "0.1.0"
description = "{{{{ project_description }}}}"
authors = [{{name = "{{{{ author_name }}}}", email = "{{{{ author_email }}}}"}}]
dependencies = ["click>=8.0.0"]

[project.scripts]
{{{{ command_name }}}} = "{{{{ package_name }}}}.__main__:main"
""",
            os.path.join(pkg_dir, "__init__.py"): "",
            os.path.join(pkg_dir, "__main__.py"): """#!/usr/bin/env python3
\"\"\"Main entry point.\"\"\"

import click

@click.command()
@click.option("--name", default="World", help="Name to greet")
def main(name: str):
    \"\"\"Main function.\"\"\"
    click.echo(f"Hello, {name}!")

if __name__ == "__main__":
    main()
""",
            os.path.join(tests_dir, "__init__.py"): "",
            os.path.join(tests_dir, "test_main.py"): """\"\"\"Tests.\"\"\"

def test_import():
    \"\"\"Test that package imports.\"\"\"
    pass
"""
        }
        
        for filepath, content in files.items():
            with open(filepath, "w") as f:
                f.write(content)
    
    def _create_python_library_template(self):
        """Create a Python library template."""
        template_dir = os.path.join(self.templates_dir, "python-library")
        if os.path.exists(template_dir):
            return
        
        os.makedirs(template_dir)
        
        metadata = {
            "name": "python-library",
            "description": "Python library template",
            "tags": ["python", "library", "package"],
            "version": "1.0.0"
        }
        
        with open(os.path.join(template_dir, "template.yaml"), "w") as f:
            yaml.dump(metadata, f)
        
        pkg_dir = os.path.join(template_dir, "src", "{{ package_name }}")
        os.makedirs(pkg_dir, exist_ok=True)
        tests_dir = os.path.join(template_dir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        
        files = {
            os.path.join(template_dir, "README.md"): f"""# {{{{ project_name }}}}

{{{{ project_description }}}}

## Installation

```bash
pip install {{{{ project_name }}}}
```
""",
            os.path.join(template_dir, "pyproject.toml"): f"""[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{{{{ project_name }}}}"
version = "0.1.0"
description = "{{{{ project_description }}}}"
authors = [{{name = "{{{{ author_name }}}}", email = "{{{{ author_email }}}}"}}]
""",
            os.path.join(pkg_dir, "__init__.py"): f"""\"\"\"{{{{ project_name }}}} package.\"\"\"

__version__ = "0.1.0"
""",
            os.path.join(pkg_dir, "core.py"): """def process(data: str) -> str:
    \"\"\"Process data.\"\"\"
    return data.upper()
""",
            os.path.join(tests_dir, "__init__.py"): "",
            os.path.join(tests_dir, "test_core.py"): """\"\"\"Tests.\"\"\"

from {{ package_name }}.core import process

def test_process():
    \"\"\"Test process function.\"\"\"
    assert process("hello") == "HELLO"
"""
        }
        
        for filepath, content in files.items():
            with open(filepath, "w") as f:
                f.write(content)
    
    def _create_python_service_template(self):
        """Create a Python service template."""
        template_dir = os.path.join(self.templates_dir, "python-service")
        if os.path.exists(template_dir):
            return
        
        os.makedirs(template_dir)
        
        metadata = {
            "name": "python-service",
            "description": "Python web service template",
            "tags": ["python", "web", "service", "api"],
            "version": "1.0.0"
        }
        
        with open(os.path.join(template_dir, "template.yaml"), "w") as f:
            yaml.dump(metadata, f)
        
        pkg_dir = os.path.join(template_dir, "src", "{{ package_name }}")
        os.makedirs(pkg_dir, exist_ok=True)
        tests_dir = os.path.join(template_dir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        
        files = {
            os.path.join(template_dir, "README.md"): f"""# {{{{ project_name }}}}

{{{{ project_description }}}}

## Running

```bash
python -m {{{{ package_name }}}}.server
```
""",
            os.path.join(template_dir, "pyproject.toml"): f"""[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{{{{ project_name }}}}"
version = "0.1.0"
description = "{{{{ project_description }}}}"
dependencies = ["fastapi>=0.68.0", "uvicorn>=0.15.0"]
""",
            os.path.join(pkg_dir, "__init__.py"): f"""\"\"\"{{{{ project_name }}}} service.\"\"\"

__version__ = "0.1.0"
""",
            os.path.join(pkg_dir, "server.py"): """from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
""",
            os.path.join(template_dir, "Dockerfile"): """FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install -e .
EXPOSE 8000
CMD ["python", "-m", "{{ package_name }}.server"]
""",
            os.path.join(tests_dir, "__init__.py"): "",
            os.path.join(tests_dir, "test_server.py"): """\"\"\"Tests.\"\"\"

def test_import():
    \"\"\"Test import.\"\"\"
    pass
"""
        }
        
        for filepath, content in files.items():
            with open(filepath, "w") as f:
                f.write(content)
    
    def list_templates(self) -> List[TemplateInfo]:
        """List all available templates."""
        templates = []
        
        for item in os.listdir(self.templates_dir):
            item_path = os.path.join(self.templates_dir, item)
            if os.path.isdir(item_path):
                metadata_path = os.path.join(item_path, "template.yaml")
                if os.path.exists(metadata_path):
                    try:
                        with open(metadata_path, "r") as f:
                            metadata = yaml.safe_load(f)
                        
                        templates.append(TemplateInfo(
                            name=metadata.get("name", item),
                            description=metadata.get("description", ""),
                            tags=metadata.get("tags", []),
                            path=item_path,
                            version=metadata.get("version", "1.0.0")
                        ))
                    except Exception:
                        continue
        
        return templates
    
    def instantiate_template(self, template_name: str, destination: str, 
                           variables: Dict[str, str] = None) -> bool:
        """Instantiate a template to create a new project."""
        if variables is None:
            variables = {}
        
        templates = self.list_templates()
        template_info = None
        for t in templates:
            if t.name == template_name:
                template_info = t
                break
        
        if template_info is None:
            raise ValueError(f"Template '{template_name}' not found")
        
        dest_path = Path(destination)
        dest_path.mkdir(parents=True, exist_ok=True)
        
        self._copy_template_files(template_info.path, dest_path, variables)
        
        return True
    
    def _copy_template_files(self, src: str, dest: Path, variables: Dict[str, str]):
        """Copy template files recursively, replacing placeholders."""
        for root, dirs, files in os.walk(src):
            rel_path = os.path.relpath(root, src)
            dest_dir = dest / rel_path
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            for file in files:
                if file == "template.yaml":
                    continue
                
                src_file = os.path.join(root, file)
                dest_file = dest_dir / file
                
                with open(src_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                
                processed_content = self._replace_placeholders(content, variables)
                
                with open(dest_file, "w", encoding="utf-8") as f:
                    f.write(processed_content)
    
    def _replace_placeholders(self, content: str, variables: Dict[str, str]) -> str:
        """Replace template placeholders with actual values."""
        result = content
        for key, value in variables.items():
            placeholder = f"{{{{ {key} }}}}"
            result = result.replace(placeholder, value)
        return result


def register_init_command(cli_app):
    """Register the init command with the CLI app."""
    import click
    
    @cli_app.command()
    @click.argument('template_name')
    @click.argument('destination', type=click.Path())
    @click.option('--project-name', prompt='Project name', help='Name of the project')
    @click.option('--description', prompt='Project description', default='', help='Description')
    @click.option('--author-name', prompt='Author name', default='Your Name', help='Author name')
    @click.option('--author-email', prompt='Author email', default='your@email.com', help='Author email')
    @click.option('--package-name', help='Package name (defaults to project name)')
    @click.option('--command-name', help='CLI command name (for CLI templates)')
    def init(template_name, destination, project_name, description, 
             author_name, author_email, package_name, command_name):
        """Initialize a new project from a template."""
        manager = TemplateManager()
        
        if not package_name:
            package_name = project_name.replace("-", "_").replace(" ", "_").lower()
        if not command_name:
            command_name = project_name.replace(" ", "-").lower()
        
        variables = {
            'project_name': project_name,
            'project_description': description,
            'author_name': author_name,
            'author_email': author_email,
            'package_name': package_name,
            'command_name': command_name
        }
        
        try:
            manager.instantiate_template(template_name, destination, variables)
            click.echo(f"Project initialized from template '{template_name}' at '{destination}'")
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            return 1
        except Exception as e:
            click.echo(f"Failed to initialize project: {e}", err=True)
            return 1
