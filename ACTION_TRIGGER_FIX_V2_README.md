This V2 only changes the GitHub Actions trigger.

Reason:
workflow_dispatch requires the workflow file to exist on the default branch.
The CloudBase experiment intentionally remains only on cloudbase-p0.

V2 behavior:
Any push to cloudbase-p0 automatically runs Build CloudBase Runtime.

Use:
1. Overlay this package onto the local repository while on cloudbase-p0.
2. Commit: fix: auto-build CloudBase runtime on p0 branch
3. Push origin.
4. Open GitHub -> Actions. The build should start automatically.
