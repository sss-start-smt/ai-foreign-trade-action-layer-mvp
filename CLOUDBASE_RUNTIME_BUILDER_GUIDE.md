# FlowOrder CloudBase Runtime Builder

Purpose:
Build Linux/Python 3.11 compatible third_party dependencies in GitHub Actions,
then download the generated runtime folder and upload that folder to CloudBase.

Why:
CloudBase HTTP Python functions do not reliably install Python dependencies
during deployment. Binary dependencies must match Linux + Python 3.11.

Recommended flow:
1. Create a temporary Git branch: cloudbase-p0.
2. Overlay the earlier FlowOrder CloudBase GitHub adapter package onto that branch.
3. Overlay this builder package onto the same branch.
4. Commit and push.
5. GitHub -> Actions -> Build CloudBase Runtime -> Run workflow.
6. When green, download artifact: floworder-cloudbase-runtime-folder.
7. Extract the downloaded artifact.
8. In CloudBase use "本地上传文件夹" and select the extracted runtime folder.
9. Runtime: Python 3.11; HTTP function; function name floworder-web.
10. Do not test Agent on the free 3-second execution timeout.

The scf_bootstrap in this overlay intentionally adds third_party to PYTHONPATH.
