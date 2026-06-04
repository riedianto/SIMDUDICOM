# ==============================================================================
# SIMDUDICOM - Tag and Push Script
# ==============================================================================

$REGISTRY = "simdudicom"
$TAG = "latest"

$IMAGES = @(
    "simdudicom-backend-api",
    "simdudicom-celery-worker",
    "simdudicom-dicom-receiver",
    "simdudicom-mwl-scp",
    "simdudicom-nginx"
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "SIMDUDICOM Docker Tag & Push Utility" -ForegroundColor Cyan
Write-Host "Registry/Username: $REGISTRY" -ForegroundColor Yellow
Write-Host "Tag:               $TAG" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Tagging Images
Write-Host "`n[1/2] Tagging images..." -ForegroundColor Green
foreach ($image in $IMAGES) {
    $localImage = "$image:latest"
    $targetImage = "$REGISTRY/$image:$TAG"
    
    Write-Host "Tagging $localImage -> $targetImage"
    docker tag $localImage $targetImage
}

# 2. Pushing Images
Write-Host "`n[2/2] Pushing images to registry..." -ForegroundColor Green
$hasFailed = $false
foreach ($image in $IMAGES) {
    $targetImage = "$REGISTRY/$image:$TAG"
    Write-Host "Pushing $targetImage..." -ForegroundColor Cyan
    docker push $targetImage
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to push $targetImage" -ForegroundColor Red
        $hasFailed = $true
    } else {
        Write-Host "Successfully pushed $targetImage" -ForegroundColor Green
    }
}

Write-Host "`n==========================================" -ForegroundColor Cyan
if ($hasFailed) {
    Write-Host "Push completed with errors! Please check if you are logged in (run: docker login)." -ForegroundColor Red
} else {
    Write-Host "All images pushed successfully!" -ForegroundColor Green
}
Write-Host "==========================================" -ForegroundColor Cyan
