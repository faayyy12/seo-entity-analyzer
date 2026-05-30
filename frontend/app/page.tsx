"use client";
import { useEffect } from "react";
import { supabase } from "@/lib/supabase";
import { useRouter } from "next/navigation";
import { useState } from "react";

interface ArticleResult {
  rank: number;
  title: string;
  url: string;
  entity_count: number;
  top_entities: Record<string, number>;
}

interface AnalysisResult {
  keyword: string;
  results: ArticleResult[];
  clusters: Record<string, Record<string, number>>;
}

export default function Home() {
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState("");

  const router = useRouter();

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) router.push("/login");
    });
  }, []);

  const analyze = async () => {
    if (!keyword.trim()) return;
    setLoading(true);
    setError("");
    setData(null);
    try {
      const res = await fetch("http://127.0.0.1:8000/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword }),
      });
      const json = await res.json();
      setData(json);
    } catch (e) {
      setError("Failed to connect to backend.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-5xl mx-auto">
        <h1 className="text-3xl font-bold text-gray-800 mb-2">SEO Entity Analyzer</h1>
        <p className="text-gray-500 mb-8">Analyze entities from Google top 10 results</p>
        
      <div className="flex justify-end mb-4">
        <button
          onClick={async () => {
            await supabase.auth.signOut();
            router.push("/login");
          }}
          className="text-sm text-gray-500 hover:text-red-500 transition"
        >
          Sign Out
        </button>
      </div>

        {/* Search Input */}
        <div className="flex gap-3 mb-8">
          <input
            type="text"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && analyze()}
            placeholder="Enter keyword (e.g. 4G吃到飽)"
            className="flex-1 border border-gray-300 rounded-lg px-4 py-3 text-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            onClick={analyze}
            disabled={loading}
            className="bg-blue-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition"
          >
            {loading ? "Analyzing..." : "Analyze"}
          </button>
        </div>

        {error && <p className="text-red-500 mb-4">{error}</p>}

        {loading && (
          <div className="text-center py-12 text-gray-500">
            Scraping and analyzing top 10 results...
          </div>
        )}

        {data && (
          <>
            {/* Results Table */}
            <div className="bg-white rounded-xl shadow mb-8 overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-100">
                <h2 className="text-lg font-semibold text-gray-700">
                  Top 10 Results — Entity Count
                </h2>
              </div>
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500 uppercase text-xs">
                  <tr>
                    <th className="px-6 py-3 text-left">Rank</th>
                    <th className="px-6 py-3 text-left">Title</th>
                    <th className="px-6 py-3 text-left">Entities</th>
                    <th className="px-6 py-3 text-left">Top Entities</th>
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((r) => (
                    <tr key={r.rank} className="border-t border-gray-100 hover:bg-gray-50">
                      <td className="px-6 py-4 font-bold text-blue-600">#{r.rank}</td>
                      <td className="px-6 py-4">
                        <a href={r.url} target="_blank" rel="noreferrer" className="text-gray-800 hover:underline">
                          {r.title}
                        </a>
                      </td>
                      <td className="px-6 py-4 font-semibold">{r.entity_count}</td>
                      <td className="px-6 py-4 text-gray-500 text-xs">
                        {Object.entries(r.top_entities).map(([name, count]) => (
                          <span key={name} className="inline-block bg-blue-50 text-blue-700 rounded px-2 py-0.5 mr-1 mb-1">
                            {name} ({count})
                          </span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Entity Clusters */}
            <div className="bg-white rounded-xl shadow overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-100">
                <h2 className="text-lg font-semibold text-gray-700">Entity Clusters by Type</h2>
              </div>
              <div className="p-6 grid grid-cols-2 md:grid-cols-3 gap-4">
                {Object.entries(data.clusters).map(([type, entities]) => (
                  <div key={type} className="bg-gray-50 rounded-lg p-4">
                    <h3 className="font-semibold text-gray-700 mb-2 text-sm uppercase">{type}</h3>
                    {Object.entries(entities).map(([name, count]) => (
                      <div key={name} className="flex justify-between text-sm text-gray-600 py-0.5">
                        <span className="truncate mr-2">{name}</span>
                        <span className="font-medium text-blue-600">{count}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </main>
  );
}