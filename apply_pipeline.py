import os

root = r'd:\Work\AutoTrading\AutoTrading-Ideal-Platform'
admin_path = os.path.join(root, 'orchestrator', 'admin.py')
with open(admin_path, 'r', encoding='utf-8') as f:
    content = f.read()

start_marker = 'PROFIT_DIRECTIVES = {'
def_marker = '\ndef generate_profit_directives()'

start_idx = content.find(start_marker)
def_idx = content.find(def_marker, start_idx)

if start_idx == -1 or def_idx == -1:
    print('Markers not found')
else:
    # Part 1: agent-desktop pipeline agent
    part1 = '''PIPELINE_AGENTS = {
    "agent-desktop": {
        "role": "pipeline",
        "purpose": "Interface and surface layer between agents, humans, and desktop applications",
        "serves": ["all_agents", "humans"],
        "exposes": [
            "OS accessibility-tree driven task execution for any agent",
            "Desktop app operation (macOS, Windows, Linux) as a shared capability",
            "Human-facing interface: surfaces other agents' capabilities via desktop automation",
            "Visual debugging and observation for agent actions",
        ],
        "internal_utility": (
            "agent-desktop is the platform's human-agent interface. Other agents "
            "delegate desktop/OS interactions to it. It surfaces each agent's "
            "capabilities so humans can observe, approve, or intervene."
        ),
        "kpi": "tasks_facilitated, agents_served, human_interventions, uptime",
    },'''
    print('Part 1 ready, length:', len(part1))

print('Done!')