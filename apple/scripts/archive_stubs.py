"""Remove unreferenced Xcode stubs; preserve runtime code and unique resources."""
import pathlib
import plistlib
import re
import shutil
import subprocess


def output(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def placeholders(app):
    frameworks = sorted((app / 'Frameworks').glob('*.framework'))
    info = plistlib.loads((app / 'Info.plist').read_bytes())
    binaries = [app / info['CFBundleExecutable']]
    for framework in frameworks:
        metadata = plistlib.loads((framework / 'Info.plist').read_bytes())
        binaries.append(framework / metadata['CFBundleExecutable'])
    references = set()
    for binary in binaries:
        # otool's first indented entry for a dylib is its own LC_ID_DYLIB.
        loads = output('otool', '-l', str(binary))
        for command in re.split(r'Load command \d+\n', loads)[1:]:
            if re.search(r'cmd LC_(?:LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD|LOAD_UPWARD)_DYLIB\b', command):
                match = re.search(r'\n\s+name (.+?) \(offset', command)
                if match:
                    references.add(match.group(1))
    found = []
    for framework, binary in zip(frameworks, binaries[1:]):
        loads = output('otool', '-l', str(binary))
        # Xcode synthesizes these empty stubs with a deliberately unreachable OS version.
        sections = re.findall(r'sectname (\S+)\s+segname \S+\s+addr \S+\s+size (0x[\da-fA-F]+)', loads)
        if not sections or any(int(size, 16) for _, size in sections):
            continue
        if set(re.findall(r'\bnsyms (\d+)\b', loads)) != {'0'}:
            continue
        # Cast's resource-only stub uses the SDK's real deployment minimum.
        if framework.name != 'GoogleCast.framework' and set(re.findall(r'\bminos (\S+)', loads)) != {'100.0'}:
            continue
        install_name = re.search(r'cmd LC_ID_DYLIB\s+cmdsize \d+\s+name (.+?) \(offset', loads)
        if not install_name or any(ref.endswith(f'/{framework.name}/{binary.name}') for ref in references):
            continue
        allowed = {'Info.plist', binary.name}
        resources = [p for p in framework.rglob('*') if p.is_file()
                     and '_CodeSignature' not in p.relative_to(framework).parts
                     and p.relative_to(framework).as_posix() not in allowed]
        copied_cast_resources = []
        if framework.name == 'GoogleCast.framework':
            for resource in resources:
                relative = resource.relative_to(framework)
                destination = (app / 'GoogleCastPrivacy.bundle/PrivacyInfo.xcprivacy'
                               if relative.as_posix() == 'PrivacyInfo.xcprivacy' else app / relative)
                copied_cast_resources.append(destination.is_file()
                                             and destination.read_bytes() == resource.read_bytes())
        if not resources or (copied_cast_resources and all(copied_cast_resources)):
            found.append(framework)
    return found


def remove_placeholders(app):
    found = placeholders(app)
    for framework in found:
        shutil.rmtree(framework)
    return [framework.name for framework in found]
