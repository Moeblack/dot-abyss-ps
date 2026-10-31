using System.Reflection;
using System.Reflection.Emit;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;

internal static class BytecodeChecks
{
    internal static bool LoginTaskUsesNativeFieldCopy(string assemblyPath)
    {
        using var stream = File.OpenRead(assemblyPath);
        using var pe = new PEReader(stream);
        var metadata = pe.GetMetadataReader();
        var type = metadata.TypeDefinitions.Select(metadata.GetTypeDefinition)
            .Single(definition => metadata.GetString(definition.Name) == "LoginTaskFactory");
        var method = type.GetMethods().Select(metadata.GetMethodDefinition)
            .Single(definition => metadata.GetString(definition.Name) == "Create");
        var constructors = 0;
        var setters = 0;
        foreach (var (opcode, reference) in ReferencedMethods(metadata, pe.GetMethodBody(method.RelativeVirtualAddress).GetILBytes()!))
        {
            if (GenericTypeName(metadata, reference.Parent) != "UniTask`1") continue;
            var name = metadata.GetString(reference.Name);
            if (opcode == OpCodes.Newobj)
            {
                var signature = metadata.GetBlobReader(reference.Signature);
                signature.ReadSignatureHeader();
                if (signature.ReadCompressedInteger() != 2) return false;
                constructors++;
            }
            if (name == "set_result") setters++;
        }
        return constructors == 1 && setters == 1;
    }

    internal static bool EmptyTermsUsesLengthConstructor(string assemblyPath)
    {
        using var stream = File.OpenRead(assemblyPath);
        using var pe = new PEReader(stream);
        var metadata = pe.GetMetadataReader();
        var type = metadata.TypeDefinitions.Select(metadata.GetTypeDefinition)
            .Single(definition => metadata.GetString(definition.Name) == "NativeLoginProbe");
        var method = type.GetMethods().Select(metadata.GetMethodDefinition)
            .Single(definition => metadata.GetString(definition.Name) == "RunOnce");
        var body = pe.GetMethodBody(method.RelativeVirtualAddress).GetILBytes()!;
        var matches = 0;
        foreach (var (opcode, reference) in ReferencedMethods(metadata, body))
        {
            if (opcode != OpCodes.Newobj || GenericTypeName(metadata, reference.Parent) != "Il2CppReferenceArray`1") continue;
            var signature = metadata.GetBlobReader(reference.Signature);
            signature.ReadSignatureHeader();
            if (signature.ReadCompressedInteger() != 1 || signature.ReadSignatureTypeCode() != SignatureTypeCode.Void
                || signature.ReadSignatureTypeCode() != SignatureTypeCode.Int64) return false;
            matches++;
        }
        return matches == 1;
    }

    private static IEnumerable<(OpCode Opcode, MemberReference Reference)> ReferencedMethods(MetadataReader metadata, byte[] body)
    {
        var opcodes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
            .Where(field => field.FieldType == typeof(OpCode)).Select(field => (OpCode)field.GetValue(null)!)
            .ToDictionary(opcode => unchecked((ushort)opcode.Value));
        for (var offset = 0; offset < body.Length;)
        {
            var first = body[offset++];
            var opcode = opcodes[first == 0xfe ? (ushort)(0xfe00 | body[offset++]) : first];
            if (opcode.OperandType == OperandType.InlineMethod)
            {
                var handle = MetadataTokens.EntityHandle(BitConverter.ToInt32(body, offset));
                if (handle.Kind == HandleKind.MemberReference)
                {
                    yield return (opcode, metadata.GetMemberReference((MemberReferenceHandle)handle));
                }
            }
            offset += opcode.OperandType switch
            {
                OperandType.InlineNone => 0,
                OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2,
                OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => 4 + 4 * BitConverter.ToInt32(body, offset),
                _ => 4
            };
        }
    }

    private static string? GenericTypeName(MetadataReader metadata, EntityHandle handle)
    {
        if (handle.Kind != HandleKind.TypeSpecification) return null;
        var specification = metadata.GetTypeSpecification((TypeSpecificationHandle)handle);
        var signature = metadata.GetBlobReader(specification.Signature);
        if (signature.ReadByte() != 0x15 || signature.ReadByte() != 0x12) return null;
        var type = signature.ReadCompressedInteger();
        return (type & 3) == 1 ? metadata.GetString(metadata.GetTypeReference(MetadataTokens.TypeReferenceHandle(type >> 2)).Name) : null;
    }
}
