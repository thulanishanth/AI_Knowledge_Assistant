Write-Host "Running pylint..."
if (Test-Path "venv\\Scripts\\python.exe") {
    & "venv\\Scripts\\python.exe" -m pylint app --rcfile=.pylintrc
} else {
    py -m pylint app --rcfile=.pylintrc
}
