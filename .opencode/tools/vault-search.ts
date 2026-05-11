import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Search your Obsidian vault for notes matching a query. Uses the retriever service (sqlite-vec + FTS5 hybrid search). Returns file paths, content snippets, and relevance scores.",
  args: {
    query: tool.schema.string().describe("Natural language search query"),
    top_k: tool.schema.number().default(5).describe("Number of results to return (default 5)"),
    include_content: tool.schema.boolean().default(true).describe("Include full chunk content in results"),
  },
  async execute(args) {
    const resp = await fetch("http://localhost:42000/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: args.query, top_k: args.top_k }),
    })
    if (!resp.ok) {
      return `Retriever error: ${resp.status} ${resp.statusText}`
    }
    const data = await resp.json()
    if (!data.results || data.results.length === 0) {
      return `No results found for: "${args.query}"`
    }
    return data.results.map((r: any) => {
      let out = `## ${r.filepath} (score: ${r.score})`
      if (r.parent_heading) out += `\nSection: ${r.parent_heading}`
      if (args.include_content) out += `\n${r.content.slice(0, 2000)}`
      return out
    }).join("\n\n---\n\n")
  },
})

export const per_source = tool({
  description: "Search only a specific file or subdirectory in your Obsidian vault",
  args: {
    query: tool.schema.string().describe("Natural language search query"),
    path_filter: tool.schema.string().describe("Filter results to a specific file or directory (e.g. 'networking/' or 'projects/ideas.md')"),
    top_k: tool.schema.number().default(5).describe("Number of results to return (default 5)"),
  },
  async execute(args) {
    const resp = await fetch("http://localhost:42000/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: args.query, top_k: args.top_k * 2 }),
    })
    if (!resp.ok) {
      return `Retriever error: ${resp.status} ${resp.statusText}`
    }
    const data = await resp.json()
    let results = data.results || []
    results = results.filter((r: any) => r.filepath.startsWith(args.path_filter))
    results = results.slice(0, args.top_k)
    if (results.length === 0) {
      return `No results in "${args.path_filter}" for: "${args.query}"`
    }
    return results.map((r: any) => {
      let out = `## ${r.filepath} (score: ${r.score})`
      if (r.parent_heading) out += `\nSection: ${r.parent_heading}`
      out += `\n${r.content.slice(0, 2000)}`
      return out
    }).join("\n\n---\n\n")
  },
})
