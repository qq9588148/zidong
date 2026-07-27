using System.ComponentModel;
using System.Security.Cryptography;
using System.Text.Json;

namespace ChampionFollow.DeviceIdentity;

internal static class Program
{
    public static int Main()
    {
        try
        {
            if (!OperatingSystem.IsWindows())
            {
                return WriteError("WINDOWS_REQUIRED");
            }

            var line = Console.ReadLine();
            if (string.IsNullOrWhiteSpace(line))
            {
                return WriteError("INVALID_REQUEST");
            }

            using var document = JsonDocument.Parse(line, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 8,
            });
            if (document.RootElement.ValueKind != JsonValueKind.Object)
            {
                return WriteError("INVALID_REQUEST");
            }

            var request = document.RootElement;
            var command = RequiredString(request, "command");
            return command switch
            {
                "public_key_spki_der" => PublicKey(request),
                "sign_ecdsa_sha256_der" => Sign(request),
                "credential_write" => CredentialWrite(request),
                "credential_read" => CredentialRead(request),
                "credential_delete" => CredentialDelete(request),
                _ => WriteError("UNKNOWN_COMMAND"),
            };
        }
        catch (JsonException)
        {
            return WriteError("INVALID_REQUEST");
        }
        catch (FormatException)
        {
            return WriteError("INVALID_BASE64");
        }
        catch (CryptographicException)
        {
            return WriteError("CRYPTOGRAPHIC_FAILURE");
        }
        catch (Win32Exception)
        {
            return WriteError("WINDOWS_STORE_FAILURE");
        }
        catch (ArgumentException)
        {
            return WriteError("INVALID_REQUEST");
        }
        catch
        {
            return WriteError("NATIVE_HELPER_FAILURE");
        }
    }

    private static int PublicKey(JsonElement request)
    {
        using var store = new DeviceKeyStore(RequiredString(request, "keyName"));
        return WriteSuccess(new
        {
            ok = true,
            publicKeySpkiDerBase64 = store.GetOrCreatePublicKeySpkiDerBase64(),
        });
    }

    private static int Sign(JsonElement request)
    {
        using var store = new DeviceKeyStore(RequiredString(request, "keyName"));
        var payload = Convert.FromBase64String(RequiredString(request, "payloadBase64"));
        try
        {
            return WriteSuccess(new
            {
                ok = true,
                signatureDerBase64 = Convert.ToBase64String(store.SignSha256Der(payload)),
            });
        }
        finally
        {
            CryptographicOperations.ZeroMemory(payload);
        }
    }

    private static int CredentialWrite(JsonElement request)
    {
        CredentialStore.Write(
            RequiredString(request, "target"),
            RequiredString(request, "value", allowEmpty: true));
        return WriteSuccess(new { ok = true });
    }

    private static int CredentialRead(JsonElement request)
    {
        var value = CredentialStore.Read(RequiredString(request, "target"));
        return WriteSuccess(new { ok = true, value });
    }

    private static int CredentialDelete(JsonElement request)
    {
        CredentialStore.Delete(RequiredString(request, "target"));
        return WriteSuccess(new { ok = true });
    }

    private static string RequiredString(
        JsonElement request,
        string name,
        bool allowEmpty = false)
    {
        if (!request.TryGetProperty(name, out var property) ||
            property.ValueKind != JsonValueKind.String)
        {
            throw new ArgumentException("Missing field.");
        }

        var value = property.GetString() ?? throw new ArgumentException("Missing field.");
        if ((!allowEmpty && string.IsNullOrWhiteSpace(value)) || value.Length > 8192)
        {
            throw new ArgumentException("Invalid field.");
        }

        return value;
    }

    private static int WriteSuccess<T>(T response)
    {
        Console.Out.WriteLine(JsonSerializer.Serialize(response));
        return 0;
    }

    private static int WriteError(string code)
    {
        Console.Out.WriteLine(JsonSerializer.Serialize(new { ok = false, error = code }));
        return 1;
    }
}
