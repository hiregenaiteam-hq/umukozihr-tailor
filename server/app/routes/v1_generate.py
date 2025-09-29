import uuid, os, logging
import datetime
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.models import GenerateRequest
from app.core.tailor import run_tailor
from app.core.tex_compile import render_tex, compile_tex, bundle
from app.db.database import get_db
from app.db.models import User
from app.auth.auth import verify_token
from datetime import datetime
import uuid as python_uuid

logger = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBearer(auto_error=False)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """Optional auth - returns user_id if authenticated, None otherwise"""
    if not credentials:
        return None
    
    token = credentials.credentials
    payload = verify_token(token)
    if not payload:
        return None
    
    try:
        # Convert string UUID from JWT back to UUID object for database query
        user_id_str = payload["sub"]
        user_id_uuid = python_uuid.UUID(user_id_str)
        
        # Verify user exists
        user = db.query(User).filter(User.id == user_id_uuid).first()
        return str(user.id) if user else None
    except (ValueError, KeyError) as e:
        logger.error(f"Error processing user token: {e}")
        return None

@router.post("/generate")
def generate(request: GenerateRequest, user_id: str = Depends(get_current_user)):
    """Generate documents - works with or without auth (for now, queue disabled)"""
    run_id = str(uuid.uuid4())
    logger.info(f"Starting document generation for run_id: {run_id}")
    logger.info(f"User authenticated: {bool(user_id)}, Jobs count: {len(request.jobs)}")
    
    # Process synchronously for all users (auth just provides tracking)
    artifacts = []
    for j in request.jobs:
        logger.info(f"Processing job: {j.id or j.title} for company: {j.company}")
        try:
            out = run_tailor(request.profile, j)
            logger.info(f"LLM processing completed for job: {j.id or j.title}")
        except Exception as e:
            logger.error(f"LLM/validation error for job {j.id or j.title}: {e}")
            raise HTTPException(400, f"LLM/validation error: {e}")
        
        base = f"{run_id}_{(j.id or j.title).replace(' ', '_')}"
        resume_ctx = {"profile": request.profile.model_dump(), "out": out.resume.model_dump(), "job": j.model_dump()}
        cover_letter_ctx = {"profile": request.profile.model_dump(), "out": out.cover_letter.model_dump(), "job": j.model_dump()}

        resume_tex_path, cover_letter_tex_path = render_tex(resume_ctx, cover_letter_ctx, j.region, base)

        # Compile to PDFs - this is the primary goal
        logger.info(f"Starting PDF compilation for job: {j.id or j.title}")
        resume_pdf_success = compile_tex(resume_tex_path)
        cover_letter_pdf_success = compile_tex(cover_letter_tex_path)
        
        # Check PDF paths
        resume_pdf_path = resume_tex_path.replace('.tex', '.pdf')
        cover_letter_pdf_path = cover_letter_tex_path.replace('.tex', '.pdf')
        
        artifact = {
            "job_id": j.id or j.title,
            "region": j.region,
            "resume_tex": f"/artifacts/{os.path.basename(resume_tex_path)}",
            "cover_letter_tex": f"/artifacts/{os.path.basename(cover_letter_tex_path)}",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "pdf_compilation": {
                "resume_success": resume_pdf_success,
                "cover_letter_success": cover_letter_pdf_success
            }
        }
        
        # Add PDF download links if compilation succeeded
        if resume_pdf_success and os.path.exists(resume_pdf_path):
            artifact["resume_pdf"] = f"/artifacts/{os.path.basename(resume_pdf_path)}"
            logger.info(f"Resume PDF ready for download: {artifact['resume_pdf']}")
        else:
            logger.warning(f"Resume PDF compilation failed for job {j.id or j.title} - TEX file available")
            
        if cover_letter_pdf_success and os.path.exists(cover_letter_pdf_path):
            artifact["cover_letter_pdf"] = f"/artifacts/{os.path.basename(cover_letter_pdf_path)}"
            logger.info(f"Cover letter PDF ready for download: {artifact['cover_letter_pdf']}")
        else:
            logger.warning(f"Cover letter PDF compilation failed for job {j.id or j.title} - TEX file available")
        
        # Optional: Include TEX content for preview (but PDFs are the main goal)
        if os.path.exists(resume_tex_path):
            with open(resume_tex_path, 'r', encoding='utf-8') as f:
                content = f.read()
                artifact["resume_tex_content"] = content  # Full content as required by project specs
                artifact["resume_tex_preview"] = content[:1000] + "..." if len(content) > 1000 else content
        if os.path.exists(cover_letter_tex_path):
            with open(cover_letter_tex_path, 'r', encoding='utf-8') as f:
                content = f.read()
                artifact["cover_letter_tex_content"] = content  # Full content as required by project specs
                artifact["cover_letter_tex_preview"] = content[:1000] + "..." if len(content) > 1000 else content
            
        artifacts.append(artifact)
    
    zip_path = bundle(run_id)
    logger.info(f"Document generation completed for run_id: {run_id}")
    logger.info(f"Generated {len(artifacts)} artifacts, bundle: {zip_path}")
    
    return {
        "run_id": run_id,  # Changed from "run" to "run_id" for frontend compatibility
        "run": run_id,     # Keep both for backward compatibility
        "artifacts": artifacts, 
        "zip": f"/artifacts/{os.path.basename(zip_path)}", 
        "authenticated": bool(user_id),
        "user_id": user_id,
        "status": "completed"  # Since we process synchronously, it's always completed
    }

@router.get("/status/{run_id}")
def get_generation_status(run_id: str, user_id: str = Depends(get_current_user)):
    """Get generation status - for frontend polling compatibility
    
    Since we process synchronously, this endpoint simulates async behavior
    by checking if artifacts exist for the given run_id.
    """
    logger.info(f"Status check requested for run_id: {run_id}")
    
    # Check if artifacts exist for this run_id in the artifacts directory
    artifacts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "artifacts"))
    
    # Look for files matching this run_id pattern
    matching_files = []
    if os.path.exists(artifacts_dir):
        for filename in os.listdir(artifacts_dir):
            if filename.startswith(run_id):
                matching_files.append(filename)
    
    if matching_files:
        # Process exists and completed - reconstruct artifacts list
        artifacts = []
        zip_file = None
        
        for filename in matching_files:
            if filename.endswith('.zip'):
                zip_file = f"/artifacts/{filename}"
            elif filename.endswith('.pdf'):
                # Group PDFs by job
                base_name = filename.replace(f"{run_id}_", "").replace('.pdf', '')
                # Handle both _cover and _cover_letter patterns
                job_id = base_name.split('_resume')[0].split('_cover')[0]
                
                # Find or create artifact for this job
                artifact = next((a for a in artifacts if a['job_id'] == job_id), None)
                if not artifact:
                    artifact = {
                        "job_id": job_id,
                        "region": "US",  # Default, could be extracted from filename
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat()
                    }
                    artifacts.append(artifact)
                
                if 'resume' in filename:
                    artifact["resume_pdf"] = f"/artifacts/{filename}"
                elif 'cover' in filename:  # This will match both _cover and _cover_letter
                    artifact["cover_letter_pdf"] = f"/artifacts/{filename}"
        
        return {
            "status": "completed",
            "run_id": run_id,
            "artifacts": artifacts,
            "zip": zip_file,
            "message": "Documents generated successfully"
        }
    else:
        # No artifacts found - might be processing or failed
        return {
            "status": "processing",
            "run_id": run_id,
            "message": "Documents are being generated..."
        }