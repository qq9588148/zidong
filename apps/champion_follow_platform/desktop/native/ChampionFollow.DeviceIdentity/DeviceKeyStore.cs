using System.Security.Cryptography;

namespace ChampionFollow.DeviceIdentity;

public sealed class DeviceKeyStore : IDisposable
{
    private static readonly CngProvider Provider =
        CngProvider.MicrosoftSoftwareKeyStorageProvider;

    private readonly string keyName;
    private bool disposed;

    public DeviceKeyStore(string keyName)
    {
        if (string.IsNullOrWhiteSpace(keyName) || keyName.Length > 240)
        {
            throw new ArgumentException("Invalid key name.", nameof(keyName));
        }

        this.keyName = keyName;
    }

    public string GetOrCreatePublicKeySpkiDerBase64()
    {
        ThrowIfDisposed();
        using var key = OpenOrCreate();
        using var signer = new ECDsaCng(key);
        var spkiDer = signer.ExportSubjectPublicKeyInfo();

        using var verifier = ECDsa.Create();
        verifier.ImportSubjectPublicKeyInfo(spkiDer, out var bytesRead);
        if (bytesRead != spkiDer.Length ||
            !CryptographicOperations.FixedTimeEquals(
                spkiDer,
                verifier.ExportSubjectPublicKeyInfo()))
        {
            throw new CryptographicException("Non-canonical public key.");
        }

        return Convert.ToBase64String(spkiDer);
    }

    public byte[] SignSha256Der(ReadOnlySpan<byte> payload)
    {
        ThrowIfDisposed();
        using var key = OpenOrCreate();
        using var signer = new ECDsaCng(key);
        return signer.SignData(
            payload,
            HashAlgorithmName.SHA256,
            DSASignatureFormat.Rfc3279DerSequence);
    }

    public byte[] ExportPrivateKey()
    {
        ThrowIfDisposed();
        using var key = OpenOrCreate();
        return key.Export(CngKeyBlobFormat.EccPrivateBlob);
    }

    public void Delete()
    {
        ThrowIfDisposed();
        if (!CngKey.Exists(keyName, Provider, CngKeyOpenOptions.UserKey))
        {
            return;
        }

        using var key = CngKey.Open(keyName, Provider, CngKeyOpenOptions.UserKey);
        key.Delete();
    }

    public void Dispose()
    {
        disposed = true;
    }

    private CngKey OpenOrCreate()
    {
        if (CngKey.Exists(keyName, Provider, CngKeyOpenOptions.UserKey))
        {
            return CngKey.Open(keyName, Provider, CngKeyOpenOptions.UserKey);
        }

        var parameters = new CngKeyCreationParameters
        {
            Provider = Provider,
            KeyCreationOptions = CngKeyCreationOptions.None,
            ExportPolicy = CngExportPolicies.None,
            KeyUsage = CngKeyUsages.Signing,
        };

        try
        {
            return CngKey.Create(CngAlgorithm.ECDsaP256, keyName, parameters);
        }
        catch (CryptographicException) when (
            CngKey.Exists(keyName, Provider, CngKeyOpenOptions.UserKey))
        {
            return CngKey.Open(keyName, Provider, CngKeyOpenOptions.UserKey);
        }
    }

    private void ThrowIfDisposed()
    {
        ObjectDisposedException.ThrowIf(disposed, this);
    }
}
