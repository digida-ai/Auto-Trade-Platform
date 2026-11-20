import { ArrowRight } from "lucide-react";
import { open } from "mausbot";

const PLATFORM_ROOT = "file:///D:/Work/AutoTrading/AutoTrading-Ideal-Platform/";

const COMPONENTS = [
  { name: "agent-desktop", desc: "Desktop automation for AI agents — operate desktop apps on Windows/macOS/Linux via the OS accessibility tree", path: "agent-desktop-main/" },
  { name: "agentic-inbox", desc: "Self-hosted email client with an AI agent (Cloudflare Workers)", path: "agentic-inbox-main/" },
  { name: "AutoHedge", desc: "Hedge trading", path: "AutoHedge-main/" },
  { name: "claude-ads", desc: "Ads tooling (Claude ads)", path: "claude-ads-main/" },
  { name: "FinceptTerminal", desc: "Enterprise terminal — real-time data, multi-agent research, live broker", path: "FinceptTerminal-main/" },
  { name: "FreeLLMAPI", desc: "87 free LLM providers behind one OpenAI-compatible endpoint", path: "freellmapi-main/" },
  { name: "grok-jev-router", desc: "Decision routing — Jev decides, Grok Bot executes, humans control irreversible actions", path: "grok-jev-router-main/" },
  { name: "hyperframes", desc: "Video generation", path: "hyperframes-main/" },
  { name: "LibreChat", desc: "Open-source chat platform", path: "LibreChat-main/" },
  { name: "Open-Higgsfield-AI", desc: "Open-source alternative to Higgsfield — generate AI images and videos", path: "Open-Higgsfield-AI-main/" },
  { name: "Open-LLM-VTuber", desc: "Desktop voice/avatar app", path: "Open-LLM-VTuber-main/" },
  { name: "Pix-Payment-System", desc: "Payment system adapted for African use cases (mobile money, bank transfers, multi-currency, phone-first) + PIX — Java app, Python package, Rust crate", path: "Pix-Payment-System-main/" },
  { name: "Scrapling", desc: "Web scraping", path: "Scrapling-main/" },
  { name: "spider-ai--trading-bot", desc: "Copy trading, latency arbitrage, 5-min sniping, synth-powered edge arbitrage", path: "spider-ai--trading-bot-main/" },
  { name: "vibetrading", desc: "Trading (Python package)", path: "vibetrading-main/" },
];

export default function IdealPlatformUI() {
  return (
    <main className="min-h-screen bg-zinc-50 dark:bg-zinc-900 px-6 py-10">
      <div className="max-w-4xl mx-auto">
        <header className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight text-zinc-900 dark:text-zinc-100">AutoTrading Ideal Platform</h1>
          <p className="mt-2 text-base leading-relaxed text-zinc-600 dark:text-zinc-400">
            One unified UI for the whole platform — every component, with implementation options where they apply.
          </p>
        </header>
        <ul className="grid gap-4 sm:grid-cols-2">
          {COMPONENTS.map((c) => (
            <li key={c.name}>
              <button
                onClick={() => open(PLATFORM_ROOT + c.path)}
                className="w-full text-left rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-800 p-5 hover:border-zinc-300 dark:hover:border-zinc-700 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-zinc-900 dark:text-zinc-100">{c.name}</span>
                  <ArrowRight size={18} className="text-zinc-400 dark:text-zinc-600" />
                </div>
                <p className="mt-1 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">{c.desc}</p>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
