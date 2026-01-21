using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using SoundFingerprinting;
using SoundFingerprinting.Audio;
using SoundFingerprinting.Builder;
using SoundFingerprinting.Data;
using SoundFingerprinting.Emy;
using SoundFingerprinting.InMemory;
using FFmpeg.AutoGen.Bindings.DynamicallyLoaded;

namespace SfpCli;

/// <summary>
/// SoundFingerprinting CLI - Audio fingerprinting tool for local song recognition.
/// 
/// Supports WAV, FLAC, MP3, and other formats via FFmpegAudioService.
/// 
/// Global Options:
///   --db-path <path>  Override database directory (default: exe dir or $SFP_DB_PATH)
/// 
/// Commands:
///   fingerprint <wav_file> --metadata <json_file>  - Add song with full metadata
///   query <wav_file> [seconds] [offset]            - Find matching song
///   serve                                          - Run as daemon (stdin/stdout JSON)
///   list                                           - List indexed songs
///   stats                                          - Show database statistics
///   delete <song_id>                               - Remove song from database
///   clear                                          - Clear entire database
///   help                                           - Show usage
/// </summary>
class Program
{
    // Database paths (set in Main from args or ENV)
    private static string DbDir = "";
    private static string MetadataPath = "";
    
    private static InMemoryModelService _modelService = null!;
    private static readonly IAudioService _audioService = new FFmpegAudioService();
    
    // Metadata storage - maps songId to full metadata (ConcurrentDictionary for thread-safe multi-client TCP access)
    private static ConcurrentDictionary<string, SongMetadata> _metadata = new();
    
    // TCP listener for external clients (CLI script, etc.) - port 9123
    private const int TcpPort = 9123;
    private static TcpListener? _tcpListener;
    private static CancellationTokenSource? _shutdownToken;

    static async Task<int> Main(string[] args)
    {
        // Initialize FFmpeg libraries (required for FFmpegAudioService)
        var ffmpegPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "FFmpeg", "bin", "x64");
        DynamicallyLoadedBindings.LibrariesPath = ffmpegPath;
        DynamicallyLoadedBindings.Initialize();
        
        // Parse global --db-path option
        var argsList = args.ToList();
        var dbPathIndex = argsList.FindIndex(a => a == "--db-path");
        
        if (dbPathIndex >= 0 && dbPathIndex + 1 < argsList.Count)
        {
            DbDir = argsList[dbPathIndex + 1];
            argsList.RemoveAt(dbPathIndex); // Remove --db-path
            argsList.RemoveAt(dbPathIndex); // Remove the path value
        }
        else
        {
            // Check environment variable, then default to exe directory
            DbDir = Environment.GetEnvironmentVariable("SFP_DB_PATH") 
                ?? Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fingerprint_data");
        }
        
        // Set metadata path relative to DB dir
        MetadataPath = Path.Combine(DbDir, "metadata.json");
        
        args = argsList.ToArray();
        
        if (args.Length == 0)
        {
            PrintUsage();
            return 0;
        }

        var command = args[0].ToLower();
        
        try
        {
            // Ensure DB directory exists
            Directory.CreateDirectory(DbDir);
            
            // Load existing fingerprint database and metadata
            LoadDatabase();
            LoadMetadata();
            
            var result = command switch
            {
                "fingerprint" => await Fingerprint(args.Skip(1).ToArray()),
                "query" => await Query(args.Skip(1).ToArray()),
                "serve" => await Serve(),
                "list" => List(),
                "stats" => Stats(),
                "delete" => Delete(args.Skip(1).ToArray()),
                "clear" => Clear(),
                "help" => Help(),
                _ => UnknownCommand(command)
            };
            
            return result;
        }
        catch (Exception ex)
        {
            OutputError(ex.Message);
            return 1;
        }
    }

    static async Task<int> Fingerprint(string[] args)
    {
        // Parse arguments: <wav_file> --metadata <json_file>
        if (args.Length < 3)
        {
            OutputError("Usage: fingerprint <wav_file> --metadata <json_file>");
            return 1;
        }

        var wavFile = args[0];
        string? metadataFile = null;
        
        for (int i = 1; i < args.Length; i++)
        {
            if (args[i] == "--metadata" && i + 1 < args.Length)
            {
                metadataFile = args[i + 1];
                break;
            }
        }
        
        if (metadataFile == null)
        {
            OutputError("Missing --metadata argument");
            return 1;
        }

        if (!File.Exists(wavFile))
        {
            OutputError($"WAV file not found: {wavFile}");
            return 1;
        }
        
        if (!File.Exists(metadataFile))
        {
            OutputError($"Metadata file not found: {metadataFile}");
            return 1;
        }

        //if (!wavFile.EndsWith(".wav", StringComparison.OrdinalIgnoreCase))
       // {
       //     OutputError("Only WAV files are supported. Please convert your audio to WAV first.");
       //     return 1;
       // }

        // Read metadata from JSON file
        SongMetadata meta;
        try
        {
            var metaJson = File.ReadAllText(metadataFile);
            meta = JsonSerializer.Deserialize<SongMetadata>(metaJson, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            }) ?? throw new Exception("Failed to parse metadata JSON");
        }
        catch (Exception ex)
        {
            OutputError($"Failed to read metadata: {ex.Message}");
            return 1;
        }
        
        // Validate required fields
        if (string.IsNullOrEmpty(meta.SongId))
        {
            OutputError("Metadata missing required field: songId");
            return 1;
        }
        if (string.IsNullOrEmpty(meta.Title))
        {
            OutputError("Metadata missing required field: title");
            return 1;
        }
        if (string.IsNullOrEmpty(meta.Artist))
        {
            OutputError("Metadata missing required field: artist");
            return 1;
        }

        // Check if already indexed by songId
        if (_metadata.ContainsKey(meta.SongId))
        {
            Output(new { 
                success = false, 
                skipped = true, 
                reason = "Song ID already exists",
                songId = meta.SongId 
            });
            return 0;
        }
        
        // Check for duplicate content hash
        if (!string.IsNullOrEmpty(meta.ContentHash))
        {
            var existingWithHash = _metadata.Values.FirstOrDefault(m => m.ContentHash == meta.ContentHash);
            if (existingWithHash != null)
            {
                Output(new { 
                    success = false, 
                    skipped = true, 
                    reason = "Duplicate content (same audio hash)",
                    existingSongId = existingWithHash.SongId,
                    songId = meta.SongId 
                });
                return 0;
            }
        }

        Console.Error.WriteLine($"Fingerprinting: {meta.Artist} - {meta.Title}");

        // Create track info and generate fingerprints
        var track = new TrackInfo(meta.SongId, meta.Title, meta.Artist);
        var hashes = await FingerprintCommandBuilder.Instance
            .BuildFingerprintCommand()
            .From(wavFile)
            .UsingServices(_audioService)
            .Hash();

        // Store in SoundFingerprinting database
        _modelService.Insert(track, hashes);

        // Update metadata with fingerprint count and indexedAt
        meta.FingerprintCount = hashes.Count;
        meta.IndexedAt = DateTime.UtcNow.ToString("o");
        
        // Store in our metadata dictionary
        _metadata[meta.SongId] = meta;

        // Save both databases
        SaveMetadata();
        SaveDatabase();

        // Output success
        Output(new
        {
            success = true,
            songId = meta.SongId,
            title = meta.Title,
            artist = meta.Artist,
            album = meta.Album,
            fingerprints = hashes.Count
        });

        return 0;
    }

    static async Task<int> Query(string[] args)
    {
        if (args.Length < 1)
        {
            OutputError("Usage: query <wav_file> [seconds_to_analyze] [start_at_second]");
            return 1;
        }

        var wavFile = args[0];
        
        // Parse optional arguments with validation
        int secondsToAnalyze = 10;
        int startAtSecond = 0;
        
        if (args.Length > 1 && !int.TryParse(args[1], out secondsToAnalyze))
        {
            OutputError($"Invalid seconds_to_analyze: {args[1]} (must be a number)");
            return 1;
        }
        if (args.Length > 2 && !int.TryParse(args[2], out startAtSecond))
        {
            OutputError($"Invalid start_at_second: {args[2]} (must be a number)");
            return 1;
        }
        if (secondsToAnalyze <= 0)
        {
            OutputError($"seconds_to_analyze must be > 0, got: {secondsToAnalyze}");
            return 1;
        }
        if (startAtSecond < 0)
        {
            OutputError($"start_at_second must be >= 0, got: {startAtSecond}");
            return 1;
        }

        if (!File.Exists(wavFile))
        {
            OutputError($"File not found: {wavFile}");
            return 1;
        }

       // if (!wavFile.EndsWith(".wav", StringComparison.OrdinalIgnoreCase))
        //{
       ////     OutputError("Only WAV files are supported. Please convert your audio to WAV first.");
       //     return 1;
       //s }

        if (_metadata.Count == 0)
        {
            Output(new { matched = false, message = "No songs indexed yet" });
            return 0;
        }

        Console.Error.WriteLine($"Querying: {Path.GetFileName(wavFile)} ({secondsToAnalyze}s from {startAtSecond}s)");

        // Query the database with multi-match support (top 6)
        var result = await QueryCommandBuilder.Instance
            .BuildQueryCommand()
            .From(wavFile, secondsToAnalyze, startAtSecond)
            .WithQueryConfig(config =>
            {
                config.Audio.MaxTracksToReturn = 6;
                return config;
            })
            .UsingServices(_modelService, _audioService)
            .Query();

        // Build matches array from all results
        var matches = new List<object>();
        foreach (var entry in result.ResultEntries.Take(6))
        {
            var audioResult = entry.Audio;
            if (audioResult == null) continue;
            
            var track = audioResult.Track;
            _metadata.TryGetValue(track.Id, out var meta);
            
            matches.Add(new
            {
                songId = track.Id,
                title = meta?.Title ?? track.Title,
                artist = meta?.Artist ?? track.Artist,
                album = meta?.Album,
                albumArtist = meta?.AlbumArtist,
                duration = meta?.Duration,
                trackNumber = meta?.TrackNumber,
                discNumber = meta?.DiscNumber,
                genre = meta?.Genre,
                year = meta?.Year,
                isrc = meta?.Isrc,
                confidence = audioResult.Confidence,
                trackMatchStartsAt = audioResult.TrackMatchStartsAt,
                queryMatchStartsAt = audioResult.QueryMatchStartsAt,
                originalFilepath = meta?.OriginalFilepath
            });
        }

        if (matches.Count > 0)
        {
            Output(new
            {
                matched = true,
                matchCount = matches.Count,
                bestMatch = matches[0],  // Backward compatibility
                matches = matches
            });
        }
        else
        {
            Output(new { matched = false, matchCount = 0, message = "No match found" });
        }

        return 0;
    }

    /// <summary>
    /// Daemon mode - keeps database loaded, reads JSON commands from stdin, responds to stdout.
    /// 
    /// Commands (JSON, one per line):
    ///   {"cmd": "query", "path": "/tmp/audio.wav", "duration": 7, "offset": 0}
    ///   {"cmd": "fingerprint", "path": "/song.flac", "metadata": {...}}
    ///   {"cmd": "save"}        - Save database to disk
    ///   {"cmd": "stats"}
    ///   {"cmd": "reload"}      - Reload database from disk
    ///   {"cmd": "shutdown"}
    /// 
    /// Responses (JSON, one per line):
    ///   {"status": "ready", "songs": 308}
    ///   {"matched": true, "matchCount": 3, "bestMatch": {...}, "matches": [...]}
    ///   {"success": true, "fingerprints": 2500}
    ///   {"status": "shutdown"}
    /// 
    /// Also listens on TCP port 9123 for external clients (CLI script, etc.)
    /// TCP clients can send the same JSON commands and receive JSON responses.
    /// </summary>
    static async Task<int> Serve()
    {
        _shutdownToken = new CancellationTokenSource();
        
        // Start TCP listener for external clients (CLI script, etc.)
        _ = StartTcpListener(_shutdownToken.Token);
        
        // Output ready signal with database stats (to stdout for process owner)
        Output(new
        {
            status = "ready",
            songs = _metadata.Count,
            fingerprints = _metadata.Values.Sum(m => m.FingerprintCount),
            tcpPort = TcpPort
        });
        Console.Out.Flush();

        // Read commands from stdin until shutdown or EOF
        string? line;
        while ((line = Console.ReadLine()) != null)
        {
            line = line.Trim();
            if (string.IsNullOrEmpty(line)) continue;

            var (response, shouldShutdown) = await ProcessCommand(line);
            Console.WriteLine(response);
            Console.Out.Flush();
            
            if (shouldShutdown)
            {
                break;
            }
        }

        // Clean shutdown
        _shutdownToken.Cancel();
        _tcpListener?.Stop();
        
        return 0;
    }
    
    /// <summary>
    /// Start TCP listener for external clients. Runs in background.
    /// </summary>
    static async Task StartTcpListener(CancellationToken cancellationToken)
    {
        try
        {
            _tcpListener = new TcpListener(IPAddress.Loopback, TcpPort);
            _tcpListener.Start();
            
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    // Accept client connection
                    var client = await _tcpListener.AcceptTcpClientAsync(cancellationToken);
                    
                    // Handle client in background (fire and forget)
                    _ = HandleTcpClient(client, cancellationToken);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (ObjectDisposedException)
                {
                    break;
                }
            }
        }
        catch (SocketException ex)
        {
            // Port already in use - log but continue (stdin still works)
            Console.Error.WriteLine($"TCP listener failed to start on port {TcpPort}: {ex.Message}");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"TCP listener error: {ex.Message}");
        }
    }
    
    /// <summary>
    /// Handle a single TCP client connection.
    /// </summary>
    static async Task HandleTcpClient(TcpClient client, CancellationToken cancellationToken)
    {
        try
        {
            using (client)
            using (var stream = client.GetStream())
            using (var reader = new StreamReader(stream, Encoding.UTF8))
            using (var writer = new StreamWriter(stream, Encoding.UTF8) { AutoFlush = true })
            {
                // Send ready signal to TCP client
                var readyJson = JsonSerializer.Serialize(new
                {
                    status = "connected",
                    songs = _metadata.Count,
                    fingerprints = _metadata.Values.Sum(m => m.FingerprintCount)
                });
                await writer.WriteLineAsync(readyJson);
                
                string? line;
                while (!cancellationToken.IsCancellationRequested && 
                       (line = await reader.ReadLineAsync()) != null)
                {
                    line = line.Trim();
                    if (string.IsNullOrEmpty(line)) continue;
                    
                    // Block shutdown command from TCP clients BEFORE processing
                    // (prevents side effects like SaveDatabase/SaveMetadata from executing)
                    try
                    {
                        using var doc = JsonDocument.Parse(line);
                        var cmd = doc.RootElement.TryGetProperty("cmd", out var cmdProp) 
                            ? cmdProp.GetString()?.ToLower() ?? "" 
                            : "";
                        
                        if (cmd == "shutdown")
                        {
                            await writer.WriteLineAsync(JsonSerializer.Serialize(new { 
                                error = "Shutdown not allowed from TCP client. Only stdin owner can shutdown." 
                            }));
                            continue;  // Reject but stay connected
                        }
                    }
                    catch (JsonException)
                    {
                        // Let ProcessCommand handle the JSON error
                    }
                    
                    var (response, _) = await ProcessCommand(line);
                    await writer.WriteLineAsync(response);
                }
            }
        }
        catch (Exception ex)
        {
            // Client disconnected or error - just log and continue
            Console.Error.WriteLine($"TCP client error: {ex.Message}");
        }
    }
    
    /// <summary>
    /// Process a single JSON command and return the response.
    /// Used by both stdin and TCP handlers.
    /// </summary>
    static async Task<(string response, bool shouldShutdown)> ProcessCommand(string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            
            var cmd = root.GetProperty("cmd").GetString()?.ToLower() ?? "";

            switch (cmd)
            {
                case "query":
                    return (await HandleQueryCommandJson(root), false);
                
                case "fingerprint":
                    return (await HandleFingerprintCommandJson(root), false);
                
                case "fingerprint-batch":
                    return (await HandleFingerprintBatchCommandJson(root), false);
                
                case "save":
                    return (HandleSaveCommandJson(), false);
                
                case "stats":
                    return (HandleStatsCommandJson(), false);
                
                case "list":
                    return (HandleListCommandJson(), false);
                
                case "list-fp":
                    return (HandleListFingerprintsCommandJson(), false);
                
                case "delete":
                    return (HandleDeleteCommandJson(root), false);
                
                case "reload":
                    return (HandleReloadCommandJson(), false);
                
                case "refresh":
                    return (HandleRefreshCommandJson(), false);
                
                case "shutdown":
                    SaveDatabase();
                    SaveMetadata();
                    return (JsonSerializer.Serialize(new { status = "shutdown" }), true);
                
                default:
                    return (JsonSerializer.Serialize(new { error = $"Unknown command: {cmd}" }), false);
            }
        }
        catch (JsonException ex)
        {
            return (JsonSerializer.Serialize(new { error = $"Invalid JSON: {ex.Message}" }), false);
        }
        catch (KeyNotFoundException ex)
        {
            return (JsonSerializer.Serialize(new { error = $"Missing field: {ex.Message}" }), false);
        }
        catch (Exception ex)
        {
            return (JsonSerializer.Serialize(new { error = $"Command error: {ex.Message}" }), false);
        }
    }
    
    // ============================================================================
    // JSON-returning command handlers (used by ProcessCommand for stdin + TCP)
    // ============================================================================
    
    static async Task<string> HandleQueryCommandJson(JsonElement root)
    {
        var path = root.GetProperty("path").GetString() ?? "";
        var duration = root.TryGetProperty("duration", out var durProp) ? durProp.GetInt32() : 10;
        var offset = root.TryGetProperty("offset", out var offProp) ? offProp.GetInt32() : 0;

        if (string.IsNullOrEmpty(path) || !File.Exists(path))
        {
            return JsonSerializer.Serialize(new { matched = false, matchCount = 0, message = $"File not found: {path}" });
        }

        if (_metadata.Count == 0)
        {
            return JsonSerializer.Serialize(new { matched = false, matchCount = 0, message = "No songs indexed yet" });
        }

        var result = await QueryCommandBuilder.Instance
            .BuildQueryCommand()
            .From(path, duration, offset)
            .WithQueryConfig(config =>
            {
                config.Audio.MaxTracksToReturn = 6;
                return config;
            })
            .UsingServices(_modelService, _audioService)
            .Query();

        var matches = new List<object>();
        foreach (var entry in result.ResultEntries.Take(6))
        {
            var audioResult = entry.Audio;
            if (audioResult == null) continue;
            
            var track = audioResult.Track;
            _metadata.TryGetValue(track.Id, out var meta);
            
            matches.Add(new
            {
                songId = track.Id,
                title = meta?.Title ?? track.Title,
                artist = meta?.Artist ?? track.Artist,
                album = meta?.Album,
                albumArtist = meta?.AlbumArtist,
                duration = meta?.Duration,
                trackNumber = meta?.TrackNumber,
                discNumber = meta?.DiscNumber,
                genre = meta?.Genre,
                year = meta?.Year,
                isrc = meta?.Isrc,
                confidence = audioResult.Confidence,
                trackMatchStartsAt = audioResult.TrackMatchStartsAt,
                queryMatchStartsAt = audioResult.QueryMatchStartsAt,
                originalFilepath = meta?.OriginalFilepath
            });
        }

        if (matches.Count > 0)
        {
            return JsonSerializer.Serialize(new
            {
                matched = true,
                matchCount = matches.Count,
                bestMatch = matches[0],
                matches = matches
            });
        }
        else
        {
            return JsonSerializer.Serialize(new { matched = false, matchCount = 0, message = "No match found" });
        }
    }
    
    static async Task<string> HandleFingerprintCommandJson(JsonElement root)
    {
        try
        {
            var path = root.GetProperty("path").GetString() ?? "";
            var metaElement = root.GetProperty("metadata");
            
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                return JsonSerializer.Serialize(new { success = false, error = $"File not found: {path}" });
            }
            
            var meta = JsonSerializer.Deserialize<SongMetadata>(metaElement.GetRawText(), new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            }) ?? new SongMetadata();
            
            if (string.IsNullOrEmpty(meta.SongId) || string.IsNullOrEmpty(meta.Title) || string.IsNullOrEmpty(meta.Artist))
            {
                return JsonSerializer.Serialize(new { success = false, error = "Missing required fields: songId, title, artist" });
            }
            
            if (_metadata.ContainsKey(meta.SongId))
            {
                return JsonSerializer.Serialize(new { success = false, skipped = true, songId = meta.SongId, reason = "Already indexed" });
            }
            
            if (!string.IsNullOrEmpty(meta.ContentHash))
            {
                var existingWithHash = _metadata.Values.FirstOrDefault(m => m.ContentHash == meta.ContentHash);
                if (existingWithHash != null)
                {
                    return JsonSerializer.Serialize(new { success = false, skipped = true, songId = meta.SongId, reason = "Duplicate content hash" });
                }
            }
            
            var track = new TrackInfo(meta.SongId, meta.Title, meta.Artist);
            var hashes = await FingerprintCommandBuilder.Instance
                .BuildFingerprintCommand()
                .From(path)
                .UsingServices(_audioService)
                .Hash();
            
            _modelService.Insert(track, hashes);
            
            meta.FingerprintCount = hashes.Count;
            meta.IndexedAt = DateTime.UtcNow.ToString("o");
            _metadata[meta.SongId] = meta;
            
            return JsonSerializer.Serialize(new { success = true, songId = meta.SongId, fingerprints = hashes.Count });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { success = false, error = ex.Message });
        }
    }
    
    static async Task<string> HandleFingerprintBatchCommandJson(JsonElement root)
    {
        const int MAX_PARALLEL = 8;  // Throttle to prevent resource exhaustion
        
        try
        {
            var filesElement = root.GetProperty("files");
            var files = filesElement.EnumerateArray().ToList();
            
            if (files.Count == 0)
            {
                return JsonSerializer.Serialize(new { success = true, processed = 0, successCount = 0, results = new List<object>() });
            }
            
            // Use semaphore to limit concurrency
            var semaphore = new SemaphoreSlim(MAX_PARALLEL);
            var tasks = new List<Task<object>>();
            
            foreach (var fileElement in files)
            {
                await semaphore.WaitAsync();
                
                tasks.Add(Task.Run(async () =>
                {
                    try
                    {
                        return await FingerprintSingleFile(fileElement);
                    }
                    finally
                    {
                        semaphore.Release();
                    }
                }));
            }
            
            var results = await Task.WhenAll(tasks);
            
            // Count successes for Python client
            int successCount = 0;
            foreach (var r in results)
            {
                // Check if result has success=true
                var json = JsonSerializer.Serialize(r);
                if (json.Contains("\"success\":true"))
                {
                    successCount++;
                }
            }
            
            return JsonSerializer.Serialize(new
            {
                success = true,
                processed = results.Length,
                successCount = successCount,
                results = results
            });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { success = false, error = ex.Message });
        }
    }
    
    static string HandleSaveCommandJson()
    {
        try
        {
            SaveDatabase();
            SaveMetadata();
            return JsonSerializer.Serialize(new
            {
                success = true,
                songCount = _metadata.Count,
                fingerprintCount = _metadata.Values.Sum(m => m.FingerprintCount)
            });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { success = false, error = ex.Message });
        }
    }
    
    static string HandleStatsCommandJson()
    {
        return JsonSerializer.Serialize(new
        {
            songCount = _metadata.Count,
            fingerprintCount = _metadata.Values.Sum(m => m.FingerprintCount),
            status = "ok"
        });
    }
    
    static string HandleListCommandJson()
    {
        var songs = _metadata.Values.Select(m => new
        {
            songId = m.SongId,
            title = m.Title,
            artist = m.Artist,
            album = m.Album,
            duration = m.Duration,
            fingerprintCount = m.FingerprintCount
        }).ToList();
        
        return JsonSerializer.Serialize(new
        {
            status = "ok",
            songCount = _metadata.Count,
            totalFingerprints = _metadata.Values.Sum(m => m.FingerprintCount),
            songs = songs
        });
    }
    
    static string HandleListFingerprintsCommandJson()
    {
        try
        {
            var fpSongIds = new List<string>();
            
            foreach (var songId in _metadata.Keys)
            {
                var track = _modelService.ReadTrackById(songId);
                if (track != null)
                {
                    fpSongIds.Add(songId);
                }
            }
            
            return JsonSerializer.Serialize(new
            {
                status = "ok",
                count = fpSongIds.Count,
                songIds = fpSongIds,
                note = "Only returns IDs that exist in both fingerprint DB and were checked."
            });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { error = $"List fingerprints failed: {ex.Message}" });
        }
    }
    
    static string HandleDeleteCommandJson(JsonElement root)
    {
        try
        {
            var songId = root.GetProperty("songId").GetString() ?? "";
            
            if (string.IsNullOrEmpty(songId))
            {
                return JsonSerializer.Serialize(new { success = false, error = "Missing songId" });
            }
            
            if (!_metadata.ContainsKey(songId))
            {
                return JsonSerializer.Serialize(new { success = false, error = "Song not found in metadata" });
            }
            
            // Remove from fingerprint database
            _modelService.DeleteTrack(songId);
            
            // Remove from metadata
            _metadata.TryRemove(songId, out _);
            
            return JsonSerializer.Serialize(new { success = true, deleted = songId });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { success = false, error = ex.Message });
        }
    }
    
    static string HandleReloadCommandJson()
    {
        try
        {
            var oldCount = _metadata.Count;
            LoadDatabase();
            LoadMetadata();
            return JsonSerializer.Serialize(new
            {
                status = "reloaded",
                previousSongs = oldCount,
                currentSongs = _metadata.Count
            });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { error = $"Reload failed: {ex.Message}" });
        }
    }
    
    /// <summary>
    /// Refresh only reloads metadata.json (lighter than full reload).
    /// Use when metadata file was modified externally.
    /// </summary>
    static string HandleRefreshCommandJson()
    {
        try
        {
            var oldCount = _metadata.Count;
            LoadMetadata();
            return JsonSerializer.Serialize(new
            {
                status = "refreshed",
                previousSongs = oldCount,
                currentSongs = _metadata.Count,
                note = "Metadata reloaded from disk (fingerprint DB unchanged)"
            });
        }
        catch (Exception ex)
        {
            return JsonSerializer.Serialize(new { error = $"Refresh failed: {ex.Message}" });
        }
    }

    /// <summary>
    /// Handle query command in daemon mode - returns top 6 matches
    /// </summary>
    static async Task HandleQueryCommand(JsonElement root)
    {
        var path = root.GetProperty("path").GetString() ?? "";
        var duration = root.TryGetProperty("duration", out var durProp) ? durProp.GetInt32() : 10;
        var offset = root.TryGetProperty("offset", out var offProp) ? offProp.GetInt32() : 0;

        if (string.IsNullOrEmpty(path) || !File.Exists(path))
        {
            Output(new { matched = false, matchCount = 0, message = $"File not found: {path}" });
            return;
        }

        if (_metadata.Count == 0)
        {
            Output(new { matched = false, matchCount = 0, message = "No songs indexed yet" });
            return;
        }

        // Query with multi-match support (top 6)
        var result = await QueryCommandBuilder.Instance
            .BuildQueryCommand()
            .From(path, duration, offset)
            .WithQueryConfig(config =>
            {
                config.Audio.MaxTracksToReturn = 6;
                return config;
            })
            .UsingServices(_modelService, _audioService)
            .Query();

        // Build matches array from all results
        var matches = new List<object>();
        foreach (var entry in result.ResultEntries.Take(6))
        {
            var audioResult = entry.Audio;
            if (audioResult == null) continue;
            
            var track = audioResult.Track;
            _metadata.TryGetValue(track.Id, out var meta);
            
            matches.Add(new
            {
                songId = track.Id,
                title = meta?.Title ?? track.Title,
                artist = meta?.Artist ?? track.Artist,
                album = meta?.Album,
                albumArtist = meta?.AlbumArtist,
                duration = meta?.Duration,
                trackNumber = meta?.TrackNumber,
                discNumber = meta?.DiscNumber,
                genre = meta?.Genre,
                year = meta?.Year,
                isrc = meta?.Isrc,
                confidence = audioResult.Confidence,
                trackMatchStartsAt = audioResult.TrackMatchStartsAt,
                queryMatchStartsAt = audioResult.QueryMatchStartsAt,
                originalFilepath = meta?.OriginalFilepath
            });
        }

        if (matches.Count > 0)
        {
            Output(new
            {
                matched = true,
                matchCount = matches.Count,
                bestMatch = matches[0],  // Backward compatibility
                matches = matches
            });
        }
        else
        {
            Output(new { matched = false, matchCount = 0, message = "No match found" });
        }
    }

    /// <summary>
    /// Handle stats command in daemon mode
    /// </summary>
    static void HandleStatsCommand()
    {
        Output(new
        {
            songCount = _metadata.Count,
            fingerprintCount = _metadata.Values.Sum(m => m.FingerprintCount),
            status = "ok"
        });
    }

    /// <summary>
    /// Handle list-fp command - list all songIds from fingerprint DB (not metadata).
    /// Used for database verification to detect orphan fingerprints.
    /// </summary>
    static void HandleListFingerprintsCommand()
    {
        try
        {
            // Get all track IDs from the fingerprint database
            // InMemoryModelService stores tracks internally, we query for each known ID
            var fpSongIds = new List<string>();
            
            // Try to get track info for each songId we know about from metadata
            // AND detect if there are tracks in the model that aren't in metadata
            foreach (var songId in _metadata.Keys)
            {
                var track = _modelService.ReadTrackById(songId);
                if (track != null)
                {
                    fpSongIds.Add(songId);
                }
            }
            
            Output(new
            {
                status = "ok",
                count = fpSongIds.Count,
                songIds = fpSongIds,
                note = "Only returns IDs that exist in both fingerprint DB and were checked. For full orphan detection, use 'list' command counts comparison."
            });
        }
        catch (Exception ex)
        {
            OutputError($"List fingerprints failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Handle reload command - reload database from disk
    /// </summary>
    static void HandleReloadCommand()
    {
        try
        {
            var oldCount = _metadata.Count;
            LoadDatabase();
            LoadMetadata();
            Output(new
            {
                status = "reloaded",
                previousSongs = oldCount,
                currentSongs = _metadata.Count
            });
        }
        catch (Exception ex)
        {
            OutputError($"Reload failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Handle fingerprint command in daemon mode - fast indexing without reloading
    /// </summary>
    static async Task HandleFingerprintCommand(JsonElement root)
    {
        try
        {
            var path = root.GetProperty("path").GetString() ?? "";
            var metadataObj = root.GetProperty("metadata");
            
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                Output(new { success = false, error = $"File not found: {path}" });
                return;
            }
            
            // Parse metadata from JSON
            var meta = new SongMetadata
            {
                SongId = metadataObj.GetProperty("songId").GetString() ?? "",
                Title = metadataObj.GetProperty("title").GetString() ?? "",
                Artist = metadataObj.GetProperty("artist").GetString() ?? "",
                Album = metadataObj.TryGetProperty("album", out var a) ? a.GetString() : null,
                AlbumArtist = metadataObj.TryGetProperty("albumArtist", out var aa) ? aa.GetString() : null,
                Duration = metadataObj.TryGetProperty("duration", out var d) && d.ValueKind == JsonValueKind.Number ? d.GetDouble() : null,
                TrackNumber = metadataObj.TryGetProperty("trackNumber", out var tn) && tn.ValueKind == JsonValueKind.Number ? tn.GetInt32() : null,
                DiscNumber = metadataObj.TryGetProperty("discNumber", out var dn) && dn.ValueKind == JsonValueKind.Number ? dn.GetInt32() : null,
                Genre = metadataObj.TryGetProperty("genre", out var g) ? g.GetString() : null,
                Year = metadataObj.TryGetProperty("year", out var y) ? y.GetString() : null,
                Isrc = metadataObj.TryGetProperty("isrc", out var i) ? i.GetString() : null,
                OriginalFilepath = metadataObj.TryGetProperty("originalFilepath", out var of) ? of.GetString() : null,
                ContentHash = metadataObj.TryGetProperty("contentHash", out var ch) ? ch.GetString() : null
            };
            
            // Validate required fields
            if (string.IsNullOrEmpty(meta.SongId) || string.IsNullOrEmpty(meta.Title) || string.IsNullOrEmpty(meta.Artist))
            {
                Output(new { success = false, error = "Missing required fields: songId, title, artist" });
                return;
            }
            
            // Check if already indexed
            if (_metadata.ContainsKey(meta.SongId))
            {
                Output(new { success = false, skipped = true, reason = "Already indexed", songId = meta.SongId });
                return;
            }
            
            // Check for duplicate content hash
            if (!string.IsNullOrEmpty(meta.ContentHash))
            {
                var existingWithHash = _metadata.Values.FirstOrDefault(m => m.ContentHash == meta.ContentHash);
                if (existingWithHash != null)
                {
                    Output(new { 
                        success = false, 
                        skipped = true, 
                        reason = "Duplicate content hash",
                        existingSongId = existingWithHash.SongId,
                        songId = meta.SongId 
                    });
                    return;
                }
            }
            
            // Fingerprint the file
            var track = new TrackInfo(meta.SongId, meta.Title, meta.Artist);
            var hashes = await FingerprintCommandBuilder.Instance
                .BuildFingerprintCommand()
                .From(path)
                .UsingServices(_audioService)
                .Hash();
            
            // Store in database
            _modelService.Insert(track, hashes);
            
            // Update metadata (ConcurrentDictionary is thread-safe)
            meta.FingerprintCount = hashes.Count;
            meta.IndexedAt = DateTime.UtcNow.ToString("o");
            _metadata[meta.SongId] = meta;
            
            Output(new { 
                success = true, 
                songId = meta.SongId,
                fingerprints = hashes.Count 
            });
        }
        catch (Exception ex)
        {
            Output(new { success = false, error = ex.Message });
        }
    }

    /// <summary>
    /// Handle fingerprint-batch command - parallel process multiple files (8 concurrent)
    /// 
    /// Request format:
    ///   {"cmd": "fingerprint-batch", "files": [{"path": "...", "metadata": {...}}, ...]}
    /// 
    /// Response format:
    ///   {"success": true, "processed": 8, "results": [{songId, fingerprints}, ...]}
    /// </summary>
    static async Task HandleFingerprintBatchCommand(JsonElement root)
    {
        const int MAX_PARALLEL = 8;
        
        try
        {
            var filesArray = root.GetProperty("files");
            var fileCount = filesArray.GetArrayLength();
            
            if (fileCount == 0)
            {
                Output(new { success = true, processed = 0, results = new List<object>() });
                return;
            }
            
            // Use semaphore to limit concurrency to 8
            var semaphore = new SemaphoreSlim(MAX_PARALLEL);
            var results = new List<object>();
            var tasks = new List<Task<object>>();
            
            foreach (var fileElement in filesArray.EnumerateArray())
            {
                await semaphore.WaitAsync();
                
                tasks.Add(Task.Run(async () =>
                {
                    try
                    {
                        return await FingerprintSingleFile(fileElement);
                    }
                    finally
                    {
                        semaphore.Release();
                    }
                }));
            }
            
            // Wait for all tasks to complete
            var allResults = await Task.WhenAll(tasks);
            
            // Count successes
            int successCount = 0;
            int skippedCount = 0;
            int errorCount = 0;
            
            foreach (var r in allResults)
            {
                if (r is IDictionary<string, object> dict)
                {
                    if (dict.ContainsKey("success") && (bool)dict["success"]) successCount++;
                    else if (dict.ContainsKey("skipped") && (bool)dict["skipped"]) skippedCount++;
                    else errorCount++;
                }
                results.Add(r);
            }
            
            Output(new { 
                success = true, 
                processed = fileCount,
                successCount = successCount,
                skippedCount = skippedCount,
                errorCount = errorCount,
                results = results 
            });
        }
        catch (Exception ex)
        {
            Output(new { success = false, error = $"Batch failed: {ex.Message}" });
        }
    }

    /// <summary>
    /// Fingerprint a single file (used by batch command)
    /// </summary>
    static async Task<object> FingerprintSingleFile(JsonElement fileElement)
    {
        try
        {
            var path = fileElement.GetProperty("path").GetString() ?? "";
            var metadataObj = fileElement.GetProperty("metadata");
            
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                return new { success = false, error = $"File not found: {path}" };
            }
            
            // Parse metadata from JSON
            var meta = new SongMetadata
            {
                SongId = metadataObj.GetProperty("songId").GetString() ?? "",
                Title = metadataObj.GetProperty("title").GetString() ?? "",
                Artist = metadataObj.GetProperty("artist").GetString() ?? "",
                Album = metadataObj.TryGetProperty("album", out var a) ? a.GetString() : null,
                AlbumArtist = metadataObj.TryGetProperty("albumArtist", out var aa) ? aa.GetString() : null,
                Duration = metadataObj.TryGetProperty("duration", out var d) && d.ValueKind == JsonValueKind.Number ? d.GetDouble() : null,
                TrackNumber = metadataObj.TryGetProperty("trackNumber", out var tn) && tn.ValueKind == JsonValueKind.Number ? tn.GetInt32() : null,
                DiscNumber = metadataObj.TryGetProperty("discNumber", out var dn) && dn.ValueKind == JsonValueKind.Number ? dn.GetInt32() : null,
                Genre = metadataObj.TryGetProperty("genre", out var g) ? g.GetString() : null,
                Year = metadataObj.TryGetProperty("year", out var y) ? y.GetString() : null,
                Isrc = metadataObj.TryGetProperty("isrc", out var i) ? i.GetString() : null,
                OriginalFilepath = metadataObj.TryGetProperty("originalFilepath", out var of) ? of.GetString() : null,
                ContentHash = metadataObj.TryGetProperty("contentHash", out var ch) ? ch.GetString() : null
            };
            
            // Validate required fields
            if (string.IsNullOrEmpty(meta.SongId) || string.IsNullOrEmpty(meta.Title) || string.IsNullOrEmpty(meta.Artist))
            {
                return new { success = false, songId = meta.SongId, error = "Missing required fields" };
            }
            
            // Check if already indexed (thread-safe read)
            if (_metadata.ContainsKey(meta.SongId))
            {
                return new { success = false, skipped = true, songId = meta.SongId, reason = "Already indexed" };
            }
            
            // Check for duplicate content hash (ConcurrentDictionary is thread-safe for reads)
            if (!string.IsNullOrEmpty(meta.ContentHash))
            {
                var existingWithHash = _metadata.Values.FirstOrDefault(m => m.ContentHash == meta.ContentHash);
                if (existingWithHash != null)
                {
                    return new { success = false, skipped = true, songId = meta.SongId, reason = "Duplicate content hash" };
                }
            }
            
            // Fingerprint the file
            var track = new TrackInfo(meta.SongId, meta.Title, meta.Artist);
            var hashes = await FingerprintCommandBuilder.Instance
                .BuildFingerprintCommand()
                .From(path)
                .UsingServices(_audioService)
                .Hash();
            
            // Thread-safe insert (InMemoryModelService handles locking internally)
            _modelService.Insert(track, hashes);
            
            // Update metadata (ConcurrentDictionary is thread-safe)
            meta.FingerprintCount = hashes.Count;
            meta.IndexedAt = DateTime.UtcNow.ToString("o");
            _metadata[meta.SongId] = meta;
            
            return new { success = true, songId = meta.SongId, fingerprints = hashes.Count };
        }
        catch (Exception ex)
        {
            return new { success = false, error = ex.Message };
        }
    }

    /// <summary>
    /// Handle save command - persist database and metadata to disk
    /// </summary>
    static void HandleSaveCommand()
    {
        try
        {
            SaveDatabase();
            SaveMetadata();
            Output(new { 
                status = "saved", 
                songCount = _metadata.Count,
                fingerprintCount = _metadata.Values.Sum(m => m.FingerprintCount)
            });
        }
        catch (Exception ex)
        {
            OutputError($"Save failed: {ex.Message}");
        }
    }

    static int List()
    {
        var songs = _metadata.Values.Select(m => new
        {
            songId = m.SongId,
            title = m.Title,
            artist = m.Artist,
            album = m.Album,
            duration = m.Duration,
            fingerprints = m.FingerprintCount,
            indexedAt = m.IndexedAt
        }).ToList();

        Output(new { count = songs.Count, songs = songs });
        return 0;
    }

    static int Stats()
    {
        Output(new
        {
            songCount = _metadata.Count,
            totalFingerprints = _metadata.Values.Sum(m => m.FingerprintCount),
            dbPath = DbDir,
            metadataPath = MetadataPath,
            metadataExists = File.Exists(MetadataPath),
            fingerprintDbExists = Directory.Exists(Path.Combine(DbDir, "fingerprints"))
        });
        return 0;
    }
    
    static int Delete(string[] args)
    {
        if (args.Length < 1)
        {
            OutputError("Usage: delete <song_id>");
            return 1;
        }
        
        var songId = args[0];
        
        if (!_metadata.ContainsKey(songId))
        {
            OutputError($"Song not found: {songId}");
            return 1;
        }
        
        // Remove from SoundFingerprinting
        _modelService.DeleteTrack(songId);
        
        // Remove from metadata (ConcurrentDictionary uses TryRemove)
        _metadata.TryRemove(songId, out _);
        
        // Save changes
        SaveMetadata();
        SaveDatabase();
        
        Output(new { success = true, deleted = songId });
        return 0;
    }
    
    static int Clear()
    {
        var count = _metadata.Count;
        
        // Delete all tracks from model service
        foreach (var songId in _metadata.Keys.ToList())
        {
            _modelService.DeleteTrack(songId);
        }
        
        // Clear metadata
        _metadata.Clear();
        
        // Save empty state
        SaveMetadata();
        SaveDatabase();
        
        Output(new { success = true, cleared = count });
        return 0;
    }

    static int Help()
    {
        PrintUsage();
        return 0;
    }

    static int UnknownCommand(string command)
    {
        OutputError($"Unknown command: {command}");
        PrintUsage();
        return 1;
    }

    static void PrintUsage()
    {
        Console.Error.WriteLine($@"
sfp-cli - SoundFingerprinting CLI v2.0

Database: {DbDir}

IMPORTANT: Only WAV files are supported!
Convert FLAC/MP3 to WAV before using this tool.

Global Options:
  --db-path <path>    Override database directory (or set $SFP_DB_PATH)

Commands:
  fingerprint <file.wav> --metadata <meta.json>  - Add to database
  query <file.wav> [seconds] [offset]            - Find match
  list                                           - Show indexed songs
  stats                                          - Show statistics
  delete <song_id>                               - Remove song
  clear                                          - Clear entire database
  help                                           - This message

Metadata JSON format:
{{
  ""songId"": ""artist_title"",
  ""title"": ""Song Title"",
  ""artist"": ""Artist Name"",
  ""album"": ""Album Name"",
  ""albumArtist"": ""Album Artist"",
  ""duration"": 248.3,
  ""trackNumber"": 1,
  ""discNumber"": 1,
  ""genre"": ""Metal"",
  ""year"": ""2021"",
  ""isrc"": ""ABC123"",
  ""originalFilepath"": ""E:/Music/song.flac"",
  ""contentHash"": ""abc123...""
}}

Output: JSON on stdout, progress on stderr
");
    }

    static void Output(object data)
    {
        var json = JsonSerializer.Serialize(data, new JsonSerializerOptions
        {
            WriteIndented = false,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
        });
        Console.WriteLine(json);
    }

    static void OutputError(string message)
    {
        Output(new { error = message });
    }

    static void LoadDatabase()
    {
        var fingerprintPath = Path.Combine(DbDir, "fingerprints");
        
        // Store reference to old service for cleanup
        var oldService = _modelService;
        
        // Load fingerprint database from directory if it exists
        if (Directory.Exists(fingerprintPath))
        {
            try
            {
                _modelService = new InMemoryModelService(fingerprintPath);
                Console.Error.WriteLine($"Loaded fingerprint database from {fingerprintPath}");
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Warning: Could not load database: {ex.Message}");
                _modelService = new InMemoryModelService();
            }
        }
        else
        {
            _modelService = new InMemoryModelService();
        }
        
        // Cleanup old service if this was a reload (not initial load)
        if (oldService != null)
        {
            // Grace period: let any in-flight queries on old service complete
            Thread.Sleep(500);
            
            // Dispose old service if it implements IDisposable
            (oldService as IDisposable)?.Dispose();
            
            // Force garbage collection to free the ~3GB memory
            GC.Collect();
            GC.WaitForPendingFinalizers();
            
            Console.Error.WriteLine("Cleaned up old fingerprint database from memory");
        }
    }

    static void SaveDatabase()
    {
        var fingerprintPath = Path.Combine(DbDir, "fingerprints");
        
        // Save fingerprint database to directory
        try
        {
            _modelService.Snapshot(fingerprintPath);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Warning: Could not save database: {ex.Message}");
        }
    }

    static void LoadMetadata()
    {
        if (File.Exists(MetadataPath))
        {
            try
            {
                var json = File.ReadAllText(MetadataPath);
                var dict = JsonSerializer.Deserialize<Dictionary<string, SongMetadata>>(json, new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true
                }) ?? new();
                _metadata = new ConcurrentDictionary<string, SongMetadata>(dict);
            }
            catch
            {
                _metadata = new ConcurrentDictionary<string, SongMetadata>();
            }
        }
    }

    static void SaveMetadata()
    {
        // Create snapshot for serialization (ConcurrentDictionary iteration is thread-safe)
        var snapshot = _metadata.ToDictionary(kvp => kvp.Key, kvp => kvp.Value);
        var json = JsonSerializer.Serialize(snapshot, new JsonSerializerOptions 
        { 
            WriteIndented = true,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
        });
        File.WriteAllText(MetadataPath, json);
    }
}

/// <summary>
/// Extended song metadata - all fields extracted from audio file tags.
/// </summary>
class SongMetadata
{
    // Required fields
    public string SongId { get; set; } = "";
    public string Title { get; set; } = "";
    public string Artist { get; set; } = "";
    
    // Optional metadata from tags
    public string? Album { get; set; }
    public string? AlbumArtist { get; set; }
    public double? Duration { get; set; }
    public int? TrackNumber { get; set; }
    public int? DiscNumber { get; set; }
    public string? Genre { get; set; }
    public string? Year { get; set; }
    public string? Isrc { get; set; }
    
    // File tracking
    public string? OriginalFilepath { get; set; }
    public string? ContentHash { get; set; }
    
    // Indexing info (set by CLI)
    public int FingerprintCount { get; set; }
    public string? IndexedAt { get; set; }
}
