using System.Diagnostics;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Azure.Core;
using Microsoft.Azure.Cosmos;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

static class Probe
{
    internal static readonly string[] RequestHeaders = {
        "x-ms-documentdb-partitionkey", "x-ms-documentdb-partitionkeyrangeid",
        "x-ms-start-epk", "x-ms-end-epk", "x-ms-cosmos-start-epk", "x-ms-cosmos-end-epk",
        "x-ms-documentdb-start-epk", "x-ms-documentdb-end-epk", "x-ms-read-feed-key-type",
        "x-ms-documentdb-isquery", "x-ms-documentdb-isqueryplan", "x-ms-cosmos-is-query-plan-request",
        "x-ms-cosmos-query-plan-caching", "x-ms-cosmos-query-version", "x-ms-version",
        "x-ms-documentdb-query-enablecrosspartition", "x-ms-documentdb-query-iscontinuationexpected",
        "x-ms-documentdb-query-enable-scan", "x-ms-documentdb-populatequerymetrics",
        "x-ms-cosmos-query-execution-info", "x-ms-supported-query-features", "x-ms-continuation",
        "a-im", "if-none-match", "x-ms-cosmos-query-full-text-score-scope"
    };
    internal static readonly string[] ResponseHeaders = {
        "x-ms-request-charge", "x-ms-activity-id", "x-ms-documentdb-partitionkeyrangeid",
        "x-ms-documentdb-query-metrics", "x-ms-cosmos-index-utilization", "x-ms-substatus",
        "x-ms-retry-after-ms", "retry-after", "x-ms-request-duration-ms", "x-ms-item-count",
        "x-ms-continuation", "etag", "x-ms-cosmos-query-execution-info"
    };
    internal static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    internal static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    // Enumerate first: querying a response-only header on HttpContent.Headers throws,
    // even when the header is absent. Capture must not turn successful IO into failure.
    internal static Dictionary<string, string> SelectHeaders(IEnumerable<KeyValuePair<string, IEnumerable<string>>> headers, string[] allowed) => headers
        .Where(pair => allowed.Contains(pair.Key, StringComparer.OrdinalIgnoreCase))
        .GroupBy(pair => pair.Key, StringComparer.OrdinalIgnoreCase)
        .ToDictionary(group => group.Key.ToLowerInvariant(), group => string.Join(",", group.SelectMany(pair => pair.Value)));
    internal static Dictionary<string, string> Headers(HttpRequestMessage request) => SelectHeaders(
        request.Headers.Concat(request.Content?.Headers ?? Enumerable.Empty<KeyValuePair<string, IEnumerable<string>>>()), RequestHeaders);
    internal static Dictionary<string, string> Headers(HttpResponseMessage response) => SelectHeaders(
        response.Headers.Concat(response.Content.Headers), ResponseHeaders);
    internal static void Private(string path)
    {
        var full = Path.GetFullPath(path);
        for (var dir = Path.GetDirectoryName(full); dir != null; dir = Path.GetDirectoryName(dir))
            Require(!Directory.Exists(Path.Combine(dir, ".git")) && !File.Exists(Path.Combine(dir, ".git")), "Raw runtime must be outside Git");
    }
    internal static void Save(string path, object value)
    {
        Private(path);
        using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
        var bytes = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(value, Formatting.None));
        file.Write(bytes); file.Flush(true);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
    }
    internal static void Validate(JObject command, string library)
    {
        string sql = command.Value<string>("sql") ?? "";
        var parameters = (JArray?)command["parameters"] ?? new();
        Require(sql.StartsWith("SELECT TOP ", StringComparison.Ordinal) && sql.Contains("c.libraryId = @libraryId", StringComparison.Ordinal), "Query requires bounded owned equality");
        Require(parameters.Count(p => p.Value<string>("name") == "@libraryId" && p.Value<string>("value") == library) == 1, "Query library differs");
        var pk = command["partitionKey"];
        Require(pk == null || pk.Type == JTokenType.Null || pk is JArray a && a.Count is >= 1 and <= 2 && a[0].Value<string>() == library, "Foreign partition key");
        Require(command.Value<int>("maxItemCount") == 16, "Page bound differs");
    }
    public static async Task<int> Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--self-test")
        {
            var cmd = JObject.Parse("{\"sql\":\"SELECT TOP 16 c.id FROM c WHERE c.libraryId = @libraryId\",\"parameters\":[{\"name\":\"@libraryId\",\"value\":\"owned\"}],\"partitionKey\":[\"owned\"],\"maxItemCount\":16}");
            Validate(cmd, "owned");
            try { Validate(cmd, "foreign"); throw new Exception("foreign scope accepted"); } catch (InvalidOperationException) { }
            Require(Hash(Encoding.UTF8.GetBytes("abc")) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "hash mismatch");
            var where = (JObject)cmd.DeepClone(); where["partitionKey"] = null; Validate(where, "owned");
            var foreign = (JObject)cmd.DeepClone(); foreign["partitionKey"] = JArray.FromObject(new[] { "foreign" });
            try { Validate(foreign, "owned"); throw new Exception("foreign route accepted"); } catch (InvalidOperationException) { }
            var vector = JArray.Parse("[0.0,0.12347412109375,-0.5]");
            Require(JToken.DeepEquals(vector, JToken.Parse(JsonConvert.SerializeObject(vector.ToObject<object>()))), "numeric parameter serialization drift");
            var sdk = typeof(CosmosClient).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "";
            Require(sdk.StartsWith("3.63.0", StringComparison.Ordinal), "wrong loaded SDK");
            using var request = new HttpRequestMessage(HttpMethod.Get, "https://private.invalid/");
            request.Headers.Add("Authorization", "Bearer secret"); request.Headers.Add("x-ms-start-epk", "bound");
            Require(Headers(request).Count == 1 && Headers(request)["x-ms-start-epk"] == "bound", "credential header exposed");
            request.Content = new StringContent("{}");
            Require(Headers(request).Count == 1, "request content header enumeration failed");
            using var response = new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") };
            Require(Headers(response).Count == 0, "empty response header enumeration failed");
            response.Headers.Add("Retry-After", "10");
            response.Headers.Add("x-ms-request-charge", "3.5");
            response.Headers.Add("Set-Cookie", "secret");
            var selected = Headers(response);
            Require(selected.Count == 2 && selected["retry-after"] == "10" && selected["x-ms-request-charge"] == "3.5", "response header allowlist failed");
            Console.WriteLine("{\"status\":\"passed\",\"tests\":11,\"cloudCalls\":0}"); return 0;
        }
        Require(args.Length == 1, "Expected private runtime config path");
        Private(args[0]);
        var config = JObject.Parse(File.ReadAllText(args[0]));
        var endpoint = new Uri(config.Value<string>("endpoint")!);
        string library = config.Value<string>("libraryId")!;
        string database = config.Value<string>("database")!;
        string output = config.Value<string>("dotnetRawDirectory")!;
        Private(Path.Combine(output, "entry"));
        Require(!Directory.Exists(output), "Exclusive .NET capture directory already exists");
        Directory.CreateDirectory(output);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(output, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        var credential = new FileToken(config.Value<string>("credentialFile")!);
        var recorder = new WireRecorder(endpoint, database, output);
        var options = new CosmosClientOptions {
            ConnectionMode = ConnectionMode.Gateway,
            LimitToEndpoint = true,
            MaxRetryAttemptsOnRateLimitedRequests = 0,
            MaxRetryWaitTimeOnRateLimitedRequests = TimeSpan.Zero,
            RequestTimeout = TimeSpan.FromSeconds(30),
            HttpClientFactory = () => new HttpClient(recorder, disposeHandler: false),
            SerializerOptions = new CosmosSerializationOptions { PropertyNamingPolicy = CosmosPropertyNamingPolicy.Default }
        };
        using var client = new CosmosClient(endpoint.ToString(), credential, options);
        Container container = client.GetContainer(database, "chunks");
        var assembly = typeof(CosmosClient).Assembly;
        var version = assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "";
        Require(version.StartsWith("3.63.0", StringComparison.Ordinal), "Loaded Cosmos SDK is not 3.63.0");
        Console.WriteLine(JsonConvert.SerializeObject(new { status = "ready", sdk = "Microsoft.Azure.Cosmos", version, assemblySha256 = Hash(File.ReadAllBytes(assembly.Location)), runtime = Environment.Version.ToString(), gateway = true }));
        Console.Out.Flush();
        while (await Console.In.ReadLineAsync() is string line)
        {
            var command = JObject.Parse(line);
            if (command.Value<string>("op") == "stop") break;
            Validate(command, library);
            string id = command.Value<string>("id")!;
            Require(id.Length > 0 && id.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_'), "Unsafe command id");
            recorder.Current = id;
            var result = new JObject { ["id"] = id, ["sdk"] = "dotnet", ["status"] = "partial" };
            var pages = new JArray(); var rows = new JArray(); double charge = 0;
            var timer = Stopwatch.StartNew();
            try
            {
                credential.Check();
                var query = new QueryDefinition(command.Value<string>("sql"));
                foreach (JObject p in (JArray)command["parameters"]!) query.WithParameter(p.Value<string>("name"), p["value"]!.ToObject<object>());
                var requestOptions = new QueryRequestOptions { MaxItemCount = 16, MaxConcurrency = 1, MaxBufferedItemCount = 16, PopulateIndexMetrics = true };
                if (command["partitionKey"] is JArray pk)
                {
                    var builder = new PartitionKeyBuilder();
                    foreach (var p in pk) builder.Add(p.Value<string>()!);
                    requestOptions.PartitionKey = builder.Build();
                }
                using FeedIterator<JObject> iterator = container.GetItemQueryIterator<JObject>(query, requestOptions: requestOptions);
                using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(90));
                int count = 0;
                while (iterator.HasMoreResults)
                {
                    Require(++count <= 32, "Page budget exhausted");
                    var page = await iterator.ReadNextAsync(cancellation.Token);
                    charge += page.RequestCharge;
                    foreach (var row in page) rows.Add(row);
                    pages.Add(new JObject { ["count"] = page.Count, ["requestCharge"] = page.RequestCharge, ["diagnostics"] = page.Diagnostics.ToString(), ["indexMetrics"] = page.IndexMetrics, ["activityId"] = page.ActivityId });
                    Require(rows.Count <= 1000, "Row bound exceeded");
                }
                result["status"] = "complete";
            }
            catch (Exception error)
            {
                result["errorType"] = error.GetType().Name;
                result["errorMessage"] = error.Message;
                if (error is CosmosException ce) { result["httpStatus"] = (int)ce.StatusCode; result["substatus"] = ce.SubStatusCode; result["diagnostics"] = ce.Diagnostics?.ToString(); }
            }
            finally { timer.Stop(); }
            result["rows"] = rows; result["pages"] = pages; result["sdkPageRequestCharge"] = charge;
            result["totalMs"] = timer.Elapsed.TotalMilliseconds; result["recordedWireRequests"] = recorder.Count(id);
            string path = Path.Combine(output, id + "-result.json"); Save(path, result);
            Console.WriteLine(JsonConvert.SerializeObject(new { id, status = result.Value<string>("status"), resultPath = path, sha256 = Hash(File.ReadAllBytes(path)) }));
            Console.Out.Flush(); recorder.Current = null;
        }
        return 0;
    }
}

sealed class FileToken : TokenCredential
{
    private readonly AccessToken token;
    public FileToken(string path)
    {
        Probe.Private(path); var data = JObject.Parse(File.ReadAllText(path));
        token = new AccessToken(data.Value<string>("accessToken")!, DateTimeOffset.FromUnixTimeSeconds(data.Value<long>("expires_on")));
        Check();
    }
    public void Check() => Probe.Require(token.ExpiresOn > DateTimeOffset.UtcNow.AddSeconds(120), "Shared credential expired or near expiry");
    public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken) { Check(); return token; }
    public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext requestContext, CancellationToken cancellationToken) { Check(); return ValueTask.FromResult(token); }
}

sealed class WireRecorder : DelegatingHandler
{
    private readonly Uri endpoint; private readonly string database; private readonly string output;
    private int sequence; private readonly Dictionary<string, int> counts = new(); private readonly object gate = new();
    public string? Current { get; set; }
    public WireRecorder(Uri endpoint, string database, string output)
    {
        this.endpoint = endpoint; this.database = database; this.output = output;
        InnerHandler = new SocketsHttpHandler { AllowAutoRedirect = false, MaxConnectionsPerServer = 1 };
    }
    public int Count(string id) { lock (gate) return counts.GetValueOrDefault(id); }
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var uri = request.RequestUri!;
        Probe.Require(uri.Scheme == "https" && uri.Host == endpoint.Host && uri.Port == endpoint.Port, "Unexpected service endpoint");
        bool query = request.Method == HttpMethod.Post && uri.AbsolutePath.TrimEnd('/') == $"/dbs/{Uri.EscapeDataString(database)}/colls/chunks/docs" &&
            (request.Headers.Contains("x-ms-documentdb-isquery") || request.Headers.Contains("x-ms-cosmos-is-query-plan-request") || request.Headers.Contains("x-ms-documentdb-isqueryplan"));
        Probe.Require(request.Method == HttpMethod.Get || query, "Mutation or unknown operation blocked");
        string id; int number;
        lock (gate) { id = Current ?? "background"; number = ++sequence; Probe.Require(number <= 12000, ".NET wire budget exhausted"); counts[id] = counts.GetValueOrDefault(id) + 1; }
        var body = request.Content == null ? Array.Empty<byte>() : await request.Content.ReadAsByteArrayAsync(cancellationToken);
        var record = new JObject { ["sequence"] = number, ["logicalId"] = id, ["method"] = request.Method.Method, ["url"] = uri.ToString(), ["requestHeaders"] = JObject.FromObject(Probe.Headers(request)), ["requestBodySha256"] = Probe.Hash(body), ["requestBody"] = Encoding.UTF8.GetString(body), ["startedAt"] = DateTimeOffset.UtcNow.ToString("O") };
        var timer = Stopwatch.StartNew();
        try
        {
            var response = await base.SendAsync(request, cancellationToken);
            var responseBody = await response.Content.ReadAsByteArrayAsync(cancellationToken);
            Probe.Require(responseBody.Length <= 8_000_000, "Response exceeds capture bound");
            record["httpStatus"] = (int)response.StatusCode; record["responseHeaders"] = JObject.FromObject(Probe.Headers(response));
            record["responseBodySha256"] = Probe.Hash(responseBody); record["responseBody"] = Encoding.UTF8.GetString(responseBody);
            return response;
        }
        catch (Exception error) { record["errorType"] = error.GetType().Name; record["errorMessage"] = error.Message; throw; }
        finally { timer.Stop(); record["totalMs"] = timer.Elapsed.TotalMilliseconds; Probe.Save(Path.Combine(output, $"wire-{number:D6}.json"), record); }
    }
}
