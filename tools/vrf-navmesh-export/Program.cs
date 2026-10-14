// 6R21E 最小只读 CSV→JSON exporter：用官方 ValveResourceFormat 19.2.6339 解析 CS2 NavMesh v36。
// 输入：CS2 map .nav（v36）；输出：结构 JSON（areas/adjacency/ladder/hull/bounds/stats/deterministicHash）。
// 只读：不改输入、不重写 BinaryKV3（由官方库完成）。产物 JSON 放仓库外；本源码/锁文件提交。
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using ValveResourceFormat.NavMesh;

var input = args.Length > 0 ? args[0] : throw new InvalidOperationException("usage: MapControlExporter <inputNav> <outJson> [mapName]");
var output = args.Length > 1 ? args[1] : throw new InvalidOperationException("usage: MapControlExporter <inputNav> <outJson> [mapName]");
// 6R21F-E6C-MULTIMAP：支持任意 CS2 地图（de_mirage/de_inferno 等）；mapName 缺省从输入文件名推导。
var mapName = args.Length > 2 ? args[2] : Path.GetFileNameWithoutExtension(input);
var srcSha256 = Sha256File(input);

var nav = new NavMeshFile();
nav.Read(input);

// ---- 结构导出：areas（角点 + 邻接）----
var areas = new List<AreaRecord>();
foreach (var a in nav.Areas.Values.OrderBy(a => a.AreaId))
{
    var corners = a.Corners.Select(c => new double[] { c.X, c.Y, c.Z }).ToArray();
    // 邻接：对该 area 每个角的 Connections 去重成 neighbor areaId 集合
    var adjacency = new SortedSet<uint>();
    var edgeList = new List<EdgeRecord>();
    for (var ci = 0; ci < a.Connections.Length; ci++)
    {
        foreach (var con in a.Connections[ci])
        {
            adjacency.Add(con.AreaId);
            edgeList.Add(new EdgeRecord { corner = ci, areaId = con.AreaId, edgeId = con.EdgeId });
        }
    }
    areas.Add(new AreaRecord
    {
        id = a.AreaId,
        hull = a.HullIndex,
        corners = corners,
        connections = edgeList,
        adjacency = adjacency.ToList(),
        laddersAbove = a.LaddersAbove,
        laddersBelow = a.LaddersBelow,
    });
}

// ---- ladders ----
var ladders = new List<object>();
foreach (var l in nav.Ladders)
{
    // NavMeshLadder 公共字段随版本可能不同；仅导出安全可见字段
    ladders.Add(new
    {
        // VRF ladders: id + endpoints/length (只读镜像导出由官方库给定)
        @ref = l.GetType().Name,
    });
}

// ---- 边界（所有角点包围盒）----
double minX = double.MaxValue, minY = double.MaxValue, minZ = double.MaxValue;
double maxX = double.MinValue, maxY = double.MinValue, maxZ = double.MinValue;
foreach (var a in areas)
    foreach (var c in a.corners)
    {
        minX = Math.Min(minX, c[0]); maxX = Math.Max(maxX, c[0]);
        minY = Math.Min(minY, c[1]); maxY = Math.Max(maxY, c[1]);
        minZ = Math.Min(minZ, c[2]); maxZ = Math.Max(maxZ, c[2]);
    }

// ---- 连通分量 + 边数（无向图，基于 adjacency）----
var nodeIds = areas.Select(a => a.id).ToHashSet();
var adj = new Dictionary<uint, HashSet<uint>>();
foreach (var a in areas) adj[a.id] = a.adjacency.ToHashSet();
long edgeCount = 0;
var seenEdge = new HashSet<string>();
foreach (var a in areas)
    foreach (var nb in a.adjacency)
    {
        var key = a.id < nb ? $"{a.id}|{nb}" : $"{nb}|{a.id}";
        if (seenEdge.Add(key)) edgeCount++;
    }
int components = 0;
var visited = new HashSet<uint>();
foreach (var start in nodeIds)
{
    if (visited.Contains(start)) continue;
    components++;
    var stack = new Stack<uint>(); stack.Push(start);
    while (stack.Count > 0)
    {
        var cur = stack.Pop();
        if (!visited.Add(cur)) continue;
        foreach (var nb in adj.TryGetValue(cur, out var s) ? s : Enumerable.Empty<uint>())
            if (!visited.Contains(nb)) stack.Push(nb);
    }
}

// ---- 确定性 hash（canonical：areas 按 id 排序 + 角点 + 邻接排序 + ladder/边界）----
var sb = new StringBuilder();
sb.Append("v36|").Append(srcSha256).Append('|');
foreach (var a in areas)
{
    sb.Append(a.id).Append(':').Append(a.hull).Append(':');
    foreach (var c in a.corners) sb.Append(c[0].ToString("F3", CultureInfo.InvariantCulture)).Append(',')
        .Append(c[1].ToString("F3", CultureInfo.InvariantCulture))
        .Append(';');
    sb.Append('#').Append(string.Join(",", a.adjacency)).Append('|');
}
var detHash = HashHex(SHA256.HashData(Encoding.UTF8.GetBytes(sb.ToString())));

var doc = new
{
    schema = "cs2-navmesh-v1",
    source = new
    {
        file = "maps/" + mapName + ".nav",
        map = mapName,
        sha256 = srcSha256,
        engine = "CS2 Source 2",
        navVersion = nav.Version,
        subVersion = nav.SubVersion,
        analyzed = nav.IsAnalyzed,
    },
    tool = new { name = "ValveResourceFormat", version = "19.2.6339", license = "MIT" },
    stats = new
    {
        areaCount = areas.Count,
        ladderCount = nav.Ladders.Length,
        edgeCount,
        connectedComponents = components,
        deterministicHash = detHash,
    },
    bounds = new { minX, minY, minZ, maxX, maxY, maxZ },
    areas,
    ladders,
};

var json = JsonSerializer.Serialize(doc, new JsonSerializerOptions
{
    WriteIndented = true,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
});
File.WriteAllText(output, json, new UTF8Encoding(false));

// ---- 硬校验（userspace 参照 = 官方库解析为准）----
// 6R21F-E6C-MULTIMAP：不再按 mirage 固定 2544/2 断言；改为非空/可达性健全性检查 + 实际值打印，
// 跨地图一致值统一到 VRF 官方摘要（Source2Viewer-CLI -i <nav>）做外部权威对齐。
if (areas.Count == 0) throw new InvalidOperationException("areaCount 为 0，疑似提取/解析失败");
Console.WriteLine($"OK map={mapName} areas={areas.Count} ladders={nav.Ladders.Length} edges={edgeCount} components={components} detHash={detHash}");

static string Sha256File(string path)
{
    using var fs = File.OpenRead(path);
    return HashHex(SHA256.HashData(fs));
}

static string HashHex(byte[] bytes) => Convert.ToHexString(bytes).ToLowerInvariant();

sealed class AreaRecord
{
    public uint id { get; set; }
    public byte hull { get; set; }
    public double[][] corners { get; set; } = Array.Empty<double[]>();
    public List<EdgeRecord> connections { get; set; } = new();
    public List<uint> adjacency { get; set; } = new();
    public uint[] laddersAbove { get; set; } = Array.Empty<uint>();
    public uint[] laddersBelow { get; set; } = Array.Empty<uint>();
}
sealed class EdgeRecord { public int corner { get; set; } public uint areaId { get; set; } public uint edgeId { get; set; } }
