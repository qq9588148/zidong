using System.Security.Cryptography;
using System.Text;
using ChampionFollow.DeviceIdentity;
using Xunit;

namespace ChampionFollow.DeviceIdentity.Tests;

public sealed class DeviceKeyStoreTests
{
    [Fact]
    public void ReusesNonExportableCngKeyAndProducesValidSignature()
    {
        var name = $"ChampionFollow-Test-{Guid.NewGuid():N}";
        using var store = new DeviceKeyStore(name);

        try
        {
            var publicKeyBase64 = store.GetOrCreatePublicKeySpkiDerBase64();
            Assert.Equal(publicKeyBase64, store.GetOrCreatePublicKeySpkiDerBase64());

            var spkiDer = Convert.FromBase64String(publicKeyBase64);
            using var verifier = ECDsa.Create();
            verifier.ImportSubjectPublicKeyInfo(spkiDer, out var bytesRead);
            Assert.Equal(spkiDer.Length, bytesRead);
            Assert.Equal(spkiDer, verifier.ExportSubjectPublicKeyInfo());

            var payload = Encoding.UTF8.GetBytes("fixture");
            var signatureDer = store.SignSha256Der(payload);
            Assert.True(verifier.VerifyData(
                payload,
                signatureDer,
                HashAlgorithmName.SHA256,
                DSASignatureFormat.Rfc3279DerSequence));
            Assert.ThrowsAny<CryptographicException>(() => store.ExportPrivateKey());
        }
        finally
        {
            store.Delete();
        }
    }
}
