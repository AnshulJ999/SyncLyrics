using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
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
    
    // Metadata storage - maps songId to full metadata
    private static Dictionary<string, SongMetadata> _metadata = new();

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

        // Query the database
        var result = await QueryCommandBuilder.Instance
            .BuildQueryCommand()
            .From(wavFile, secondsToAnalyze, startAtSecond)
            .UsingServices(_modelService, _audioService)
            .Query();

        if (result.BestMatch != null)
        {
            var match = result.BestMatch;
            var audioResult = match.Audio;
            
            if (audioResult != null)
            {
                var track = audioResult.Track;
                _metadata.TryGetValue(track.Id, out var meta);

                Output(new
                {
                    matched = true,
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
            else
            {
                Output(new { matched = false, message = "Audio match data not available" });
            }
        }
        else
        {
            Output(new { matched = false, message = "No match found" });
        }

        return 0;
    }

    /// <summary>
    /// Daemon mode - keeps database loaded, reads JSON commands from stdin, responds to stdout.
    /// 
    /// Commands (JSON, one per line):
    ///   {"cmd": "query", "path": "/tmp/audio.wav", "duration": 7, "offset": 0}
    ///   {"cmd": "stats"}
    ///   {"cmd": "reload"}  - Reload database from disk
    ///   {"cmd": "shutdown"}
    /// 
    /// Responses (JSON, one per line):
    ///   {"status": "ready", "songs": 308}
    ///   {"matched": true, "songId": "...", ...}
    ///   {"status": "shutdown"}
    /// </summary>
    static async Task<int> Serve()
    {
        // Output ready signal with database stats
        Output(new
        {
            status = "ready",
            songs = _metadata.Count,
            fingerprints = _metadata.Values.Sum(m => m.FingerprintCount)
        });
        Console.Out.Flush();

        // Read commands from stdin until shutdown or EOF
        string? line;
        while ((line = Console.ReadLine()) != null)
        {
            line = line.Trim();
            if (string.IsNullOrEmpty(line)) continue;

            try
            {
                // Parse JSON command
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                
                var cmd = root.GetProperty("cmd").GetString()?.ToLower() ?? "";

                switch (cmd)
                {
                    case "query":
                        await HandleQueryCommand(root);
                        break;
                    
                    case "stats":
                        HandleStatsCommand();
                        break;
                    
                    case "reload":
                        HandleReloadCommand();
                        break;
                    
                    case "shutdown":
                        Output(new { status = "shutdown" });
                        Console.Out.Flush();
                        return 0;
                    
                    default:
                        OutputError($"Unknown command: {cmd}");
                        break;
                }
            }
            catch (JsonException ex)
            {
                OutputError($"Invalid JSON: {ex.Message}");
            }
            catch (KeyNotFoundException ex)
            {
                OutputError($"Missing field: {ex.Message}");
            }
            catch (Exception ex)
            {
                OutputError($"Command error: {ex.Message}");
            }

            Console.Out.Flush();
        }

        // EOF reached (stdin closed)
        return 0;
    }

    /// <summary>
    /// Handle query command in daemon mode
    /// </summary>
    static async Task HandleQueryCommand(JsonElement root)
    {
        var path = root.GetProperty("path").GetString() ?? "";
        var duration = root.TryGetProperty("duration", out var durProp) ? durProp.GetInt32() : 10;
        var offset = root.TryGetProperty("offset", out var offProp) ? offProp.GetInt32() : 0;

        if (string.IsNullOrEmpty(path) || !File.Exists(path))
        {
            Output(new { matched = false, message = $"File not found: {path}" });
            return;
        }

        if (_metadata.Count == 0)
        {
            Output(new { matched = false, message = "No songs indexed yet" });
            return;
        }

        // Query the database (same logic as Query method)
        var result = await QueryCommandBuilder.Instance
            .BuildQueryCommand()
            .From(path, duration, offset)
            .UsingServices(_modelService, _audioService)
            .Query();

        if (result.BestMatch != null)
        {
            var match = result.BestMatch;
            var audioResult = match.Audio;
            
            if (audioResult != null)
            {
                var track = audioResult.Track;
                _metadata.TryGetValue(track.Id, out var meta);

                Output(new
                {
                    matched = true,
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
            else
            {
                Output(new { matched = false, message = "Audio match data not available" });
            }
        }
        else
        {
            Output(new { matched = false, message = "No match found" });
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
        
        // Remove from metadata
        _metadata.Remove(songId);
        
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
                _metadata = JsonSerializer.Deserialize<Dictionary<string, SongMetadata>>(json, new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true
                }) ?? new();
            }
            catch
            {
                _metadata = new();
            }
        }
    }

    static void SaveMetadata()
    {
        var json = JsonSerializer.Serialize(_metadata, new JsonSerializerOptions 
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
