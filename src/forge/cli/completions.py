"""Shell completion support for Forge CLI."""

import os
import sys
from pathlib import Path
from typing import Dict, List


class ShellCompletionGenerator:
    """Generate shell completion scripts."""
    
    def __init__(self):
        self.commands = [
            "new", "compile", "demo", "init", "completions", 
            "doctor", "tui", "memory", "status", "agents"
        ]
        
        self.global_options = ["--help", "--verbose", "--config", "--version"]
    
    def generate_bash_completion(self) -> str:
        """Generate bash completion script."""
        return '''# bash completion script for forge
_forge_completion() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="new compile demo init completions doctor tui memory status agents --help --verbose --config --version"

    case "${prev}" in
        forge)
            COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
            return 0
            ;;
        --config)
            COMPREPLY=( $(compgen -f -- ${cur}) )
            return 0
            ;;
        *)
            COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
            return 0
            ;;
    esac
}

complete -F _forge_completion forge
'''
    
    def generate_zsh_completion(self) -> str:
        """Generate zsh completion script."""
        return '''# zsh completion script for forge
#compdef forge

local -a commands
commands=(
    "new:Create a new project with graph orchestration"
    "compile:Run intent-to-production pipeline"
    "demo:Run interactive tutorial"
    "init:Initialize project from template"
    "completions:Manage shell completions"
    "doctor:Run diagnostics"
    "tui:Launch terminal UI"
    "memory:Inspect project memory"
    "status:Show project status"
    "agents:List active subagents"
    "--help:Show help"
    "--verbose:Enable verbose output"
    "--config:Specify config file"
    "--version:Show version"
)

_arguments \\
    '*:: :->subcmds' \\
    && ret=0

if (( CURRENT == 1 )); then
    _describe 'command' commands
elif (( CURRENT > 1 )); then
    case $words[2] in
        --config)
            _files
            ;;
        *)
            _arguments \\
                "--help[Show help]" \\
                "--verbose[Enable verbose output]" \\
                "--config[Specify config file]:config file:_files" \\
                "--version[Show version]"
            ;;
    esac
fi

return ret
'''
    
    def generate_fish_completion(self) -> str:
        """Generate fish completion script."""
        return '''# fish completion script for forge

function __forge_needs_command
    set cmd (commandline -opc)
    if [ (count $cmd) -eq 1 -a "$cmd[1]" != "forge" ]
        return 1
    end
    return 0
end

function __forge_using_command
    set cmd (commandline -opc)
    if [ (count $cmd) -gt 1 ]
        for arg in $cmd
            switch $arg
                case new compile demo init completions doctor tui memory status agents
                    echo $arg
                    return 0
            end
        end
    end
    return 1
end

# Main forge command completions
complete -c forge -n '__forge_needs_command' -xa "new compile demo init completions doctor tui memory status agents" -d "Forge commands"

# Global options
complete -c forge -s h -l help -d "Show help"
complete -c forge -s v -l verbose -d "Enable verbose output"
complete -c forge -l config -d "Specify config file" -r
complete -c forge -l version -d "Show version"
'''
    
    def install_completion(self, shell: str, target_path: str = None) -> bool:
        """Install completion script to appropriate location."""
        if shell not in ['bash', 'zsh', 'fish']:
            raise ValueError(f"Unsupported shell: {shell}")
        
        script_generator = {
            'bash': self.generate_bash_completion,
            'zsh': self.generate_zsh_completion,
            'fish': self.generate_fish_completion,
        }[shell]
        
        script_content = script_generator()
        
        if target_path is None:
            if shell == 'bash':
                target_path = os.path.expanduser('~/.bash_completion.d/forge')
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
            elif shell == 'zsh':
                target_path = os.path.expanduser('~/.zsh/completion/_forge')
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
            elif shell == 'fish':
                target_path = os.path.expanduser('~/.config/fish/completions/forge.fish')
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
        
        try:
            with open(target_path, 'w') as f:
                f.write(script_content)
            return True
        except Exception as e:
            print(f"Error writing completion script: {e}", file=sys.stderr)
            return False
    
    def print_completion_script(self, shell: str) -> str:
        """Print completion script to stdout."""
        if shell not in ['bash', 'zsh', 'fish']:
            raise ValueError(f"Unsupported shell: {shell}")
        
        script_generator = {
            'bash': self.generate_bash_completion,
            'zsh': self.generate_zsh_completion,
            'fish': self.generate_fish_completion,
        }[shell]
        
        return script_generator()


def register_completions_command(cli_app):
    """Register the completions command with the CLI app."""
    import click
    
    @cli_app.command()
    @click.argument('action', type=click.Choice(['install', 'print']))
    @click.option('--shell', type=click.Choice(['bash', 'zsh', 'fish']), 
                  default=None, help='Shell type (auto-detected if not provided)')
    @click.option('--output', '-o', type=click.Path(), 
                  help='Output file for printing scripts')
    def completions(action, shell, output):
        """Manage shell completions for Forge."""
        generator = ShellCompletionGenerator()
        
        if shell is None:
            shell = os.environ.get('SHELL', '').split('/')[-1]
            if shell not in ['bash', 'zsh', 'fish']:
                click.echo("Could not auto-detect shell. Please specify with --shell.", err=True)
                return 1
        
        if action == 'print':
            script = generator.print_completion_script(shell)
            if output:
                with open(output, 'w') as f:
                    f.write(script)
                click.echo(f"Completion script written to {output}")
            else:
                click.echo(script)
        elif action == 'install':
            success = generator.install_completion(shell)
            if success:
                click.echo(f"Completion script installed for {shell}")
                if shell == 'bash':
                    click.echo("Run 'source ~/.bashrc' or restart your shell to activate.")
                elif shell == 'zsh':
                    click.echo("Run 'autoload -U compinit; compinit' or restart your shell to activate.")
                elif shell == 'fish':
                    click.echo("Restart your shell to activate.")
            else:
                click.echo("Failed to install completion script.", err=True)
                return 1
