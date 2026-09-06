"""
Example usage of DeepContexts for Grok Build bridging.

DeepContexts allow you to package a workspace (attachments + tools) so that
Grok Build agents can attach them as long-term memory and custom capabilities.

This is the main mechanism for bidirectional memory between xlii and Grok Build.
"""

# Example 1: Save a workspace as a shareable DeepContext
"""
Inside an xlii REPL:

    /context save auth-refactor as company-auth-patterns

This creates ~/.config/xlii/contexts/company-auth-patterns.json containing:
- The attachments from the "auth-refactor" workspace
- All custom AgentTools currently loaded in the project
- Source project + workspace metadata

You can now hand this context to a Grok Build agent.
"""

# Example 2: From Grok Build (conceptual)
"""
In a Grok Build session you could say:

    "Attach the xlii DeepContext 'company-auth-patterns' and refactor the payment service using its patterns."

A future Grok Build skill or built-in would call:
    - xlii_load_context("company-auth-patterns")
    - Inject the refs + docs into the agent's long-term memory
    - Register the custom tools from that context
"""

# Example 3: Write-back flow (planned)
"""
After a Grok Build agent works with the context for a while:

    "Save the new patterns we discovered back to the xlii workspace."

This would eventually call:
    xlii_sync_context("company-auth-patterns", updates={...})

And xlii would merge the new attachments back into the source workspace.
"""

# Current commands available today:
#
# /context list
# /context save <workspace> [as <name>]
# /context show <name>
# /context sync <name>
# /context delete <name>
#
# These are the foundation for the full bidirectional bridge.