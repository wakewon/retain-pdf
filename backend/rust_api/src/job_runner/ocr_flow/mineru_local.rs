use anyhow::{anyhow, bail, Context, Result};
use reqwest::multipart::{Form, Part};
use serde_json::Value;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;
use zip::ZipArchive;

use crate::job_runner::{job_artifacts_mut, ocr_provider_diagnostics_mut, ProcessRuntimeDeps};
use crate::models::{now_iso, JobRuntimeState};
use crate::ocr_provider::OcrTaskHandle;

use super::artifacts::persist_provider_result;
use super::markdown_bundle::export_markdown_bundle;
use super::save_ocr_job;

const DEFAULT_MINERU_LOCAL_BASE_URL: &str = "http://host.docker.internal:18080";
const MINERU_LOCAL_TIMEOUT_SECS: u64 = 1800;

pub(super) fn resolve_mineru_local_base_url(configured: &str) -> String {
    let raw = configured.trim();
    let value = if raw.is_empty() {
        std::env::var("RETAIN_MINERU_LOCAL_BASE_URL")
            .unwrap_or_else(|_| DEFAULT_MINERU_LOCAL_BASE_URL.to_string())
    } else {
        raw.to_string()
    };
    value.trim().trim_end_matches('/').to_string()
}

pub(super) async fn run_local_ocr_transport_mineru_local(
    deps: &ProcessRuntimeDeps,
    job: &mut JobRuntimeState,
    upload_path: &Path,
    provider_result_json_path: &Path,
    provider_zip_path: &Path,
    provider_raw_dir: &Path,
    layout_json_path: &Path,
    parent_job_id: Option<&str>,
) -> Result<()> {
    let base_url = resolve_mineru_local_base_url(&job.request_payload.ocr.mineru_local_base_url);
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(30))
        .timeout(Duration::from_secs(MINERU_LOCAL_TIMEOUT_SECS))
        .build()
        .context("build MinerU local client failed")?;

    job.stage = Some("mineru_local_processing".to_string());
    job.stage_detail = Some("正在调用本地 MinerU 服务解析 PDF".to_string());
    job.updated_at = now_iso();
    {
        let diagnostics = ocr_provider_diagnostics_mut(job);
        diagnostics.handle = OcrTaskHandle {
            batch_id: None,
            task_id: None,
            file_name: upload_path
                .file_name()
                .and_then(|item| item.to_str())
                .map(|item| item.to_string()),
        };
    }
    save_ocr_job(deps, job, parent_job_id).await?;

    let response = post_file_parse(&client, &base_url, job, upload_path).await?;
    let status = response.status();
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_ascii_lowercase();
    let bytes = response
        .bytes()
        .await
        .context("failed to read MinerU local response")?;
    if !status.is_success() {
        bail!(
            "MinerU local HTTP {}: {}",
            status.as_u16(),
            summarize_bytes(&bytes)
        );
    }

    fs::create_dir_all(provider_raw_dir)?;
    if looks_like_zip(&content_type, &bytes) {
        write_bytes(provider_zip_path, &bytes)?;
        unpack_zip(provider_zip_path, provider_raw_dir)?;
    } else {
        let json_payload: Value = serde_json::from_slice(&bytes).with_context(|| {
            format!(
                "MinerU local did not return a zip and response was not JSON: {}",
                summarize_bytes(&bytes)
            )
        })?;
        persist_non_zip_response(provider_raw_dir, &json_payload)?;
        let maybe_zip = extract_zip_payload(&json_payload, provider_zip_path)?;
        if maybe_zip {
            unpack_zip(provider_zip_path, provider_raw_dir)?;
        }
    }

    let resolved_layout_json = ensure_layout_json(provider_raw_dir, layout_json_path)?;
    let result = serde_json::json!({
        "code": 0,
        "data": {
            "state": "done",
            "provider": "mineru_local",
            "base_url": base_url,
            "layout_json": resolved_layout_json,
        },
        "msg": "ok",
        "trace_id": "",
    });
    persist_provider_result(job, provider_result_json_path, &result).await?;
    ocr_provider_diagnostics_mut(job)
        .artifacts
        .provider_bundle_zip = Some(provider_zip_path.to_string_lossy().to_string());
    ocr_provider_diagnostics_mut(job).artifacts.layout_json =
        Some(layout_json_path.to_string_lossy().to_string());
    job_artifacts_mut(job).layout_json = Some(layout_json_path.to_string_lossy().to_string());
    export_markdown_bundle(
        &provider_raw_dir.to_string_lossy(),
        job_artifacts_mut(job).job_root.as_deref(),
    )?;

    job.stage = Some("translation_prepare".to_string());
    job.stage_detail = Some("本地 MinerU 解析完成，准备标准化 OCR 结果".to_string());
    job.updated_at = now_iso();
    save_ocr_job(deps, job, parent_job_id).await?;
    Ok(())
}

async fn post_file_parse(
    client: &reqwest::Client,
    base_url: &str,
    job: &JobRuntimeState,
    upload_path: &Path,
) -> Result<reqwest::Response> {
    let file_name = upload_path
        .file_name()
        .and_then(|item| item.to_str())
        .unwrap_or("upload.pdf")
        .to_string();
    let bytes = tokio::fs::read(upload_path)
        .await
        .with_context(|| format!("failed to read upload file {}", upload_path.display()))?;
    let part = Part::bytes(bytes)
        .file_name(file_name)
        .mime_str("application/pdf")
        .context("build MinerU local multipart part failed")?;
    let form = Form::new()
        .part("files", part)
        .text("parse_method", "auto")
        .text("return_md", "true")
        .text("return_middle_json", "true")
        .text("return_model_output", "true")
        .text("return_content_list", "true")
        .text("return_images", "true")
        .text("return_original_file", "true")
        .text("response_format_zip", "true")
        .text("lang", job.request_payload.ocr.language.clone())
        .text(
            "formula_enable",
            (!job.request_payload.ocr.disable_formula).to_string(),
        )
        .text(
            "table_enable",
            (!job.request_payload.ocr.disable_table).to_string(),
        );
    let url = format!("{}/file_parse", base_url.trim_end_matches('/'));
    client
        .post(url)
        .multipart(form)
        .send()
        .await
        .context("MinerU local /file_parse request failed")
}

fn looks_like_zip(content_type: &str, bytes: &[u8]) -> bool {
    content_type.contains("zip") || bytes.starts_with(b"PK\x03\x04")
}

fn write_bytes(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, bytes).with_context(|| format!("failed to write {}", path.display()))
}

fn unpack_zip(zip_path: &Path, dest_dir: &Path) -> Result<()> {
    fs::create_dir_all(dest_dir)?;
    let file =
        File::open(zip_path).with_context(|| format!("failed to open {}", zip_path.display()))?;
    let mut archive =
        ZipArchive::new(file).with_context(|| format!("invalid zip {}", zip_path.display()))?;
    for idx in 0..archive.len() {
        let mut entry = archive.by_index(idx)?;
        let Some(enclosed) = entry.enclosed_name().map(|path| path.to_owned()) else {
            continue;
        };
        let out_path = dest_dir.join(enclosed);
        if entry.is_dir() {
            fs::create_dir_all(&out_path)?;
            continue;
        }
        if let Some(parent) = out_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut writer = File::create(&out_path)?;
        io::copy(&mut entry, &mut writer)?;
        writer.flush()?;
    }
    Ok(())
}

fn persist_non_zip_response(provider_raw_dir: &Path, payload: &Value) -> Result<()> {
    let bytes =
        serde_json::to_vec_pretty(payload).context("failed to serialize MinerU local JSON response")?;
    write_bytes(
        &provider_raw_dir.join("mineru_local_response.json"),
        &bytes,
    )
}

fn extract_zip_payload(payload: &Value, provider_zip_path: &Path) -> Result<bool> {
    let Some(encoded) = find_string_field(payload, &["zip_base64", "zip", "data"]) else {
        return Ok(false);
    };
    if encoded.len() < 16 || encoded.starts_with("http://") || encoded.starts_with("https://") {
        return Ok(false);
    }
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded.as_bytes())
        .context("MinerU local JSON response contained invalid zip base64")?;
    if !bytes.starts_with(b"PK\x03\x04") {
        return Ok(false);
    }
    write_bytes(provider_zip_path, &bytes)?;
    Ok(true)
}

fn find_string_field<'a>(payload: &'a Value, names: &[&str]) -> Option<&'a str> {
    match payload {
        Value::Object(map) => {
            for name in names {
                if let Some(value) = map.get(*name).and_then(|value| value.as_str()) {
                    return Some(value);
                }
            }
            for value in map.values() {
                if let Some(found) = find_string_field(value, names) {
                    return Some(found);
                }
            }
            None
        }
        Value::Array(items) => items.iter().find_map(|item| find_string_field(item, names)),
        _ => None,
    }
}

fn ensure_layout_json(provider_raw_dir: &Path, layout_json_path: &Path) -> Result<PathBuf> {
    if layout_json_path.exists() {
        return Ok(layout_json_path.to_path_buf());
    }
    let candidate = find_layout_candidate(provider_raw_dir).ok_or_else(|| {
        anyhow!(
            "MinerU local output did not contain layout.json or *_middle.json under {}",
            provider_raw_dir.display()
        )
    })?;
    if let Some(parent) = layout_json_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::copy(&candidate, layout_json_path).with_context(|| {
        format!(
            "failed to copy MinerU local layout from {} to {}",
            candidate.display(),
            layout_json_path.display()
        )
    })?;
    Ok(layout_json_path.to_path_buf())
}

fn find_layout_candidate(root: &Path) -> Option<PathBuf> {
    let mut fallback = None;
    for entry in walkdir::WalkDir::new(root)
        .into_iter()
        .filter_map(std::result::Result::ok)
    {
        if !entry.file_type().is_file() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
        if name == "layout.json" {
            return Some(entry.path().to_path_buf());
        }
        if name.ends_with("_middle.json") || name == "middle.json" {
            fallback = Some(entry.path().to_path_buf());
        }
    }
    fallback
}

fn summarize_bytes(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let compact = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.len() > 800 {
        let preview = compact.chars().take(800).collect::<String>();
        format!("{preview}...<truncated>")
    } else {
        compact
    }
}

#[cfg(test)]
mod tests {
    use super::resolve_mineru_local_base_url;

    #[test]
    fn configured_mineru_local_base_url_wins() {
        assert_eq!(
            resolve_mineru_local_base_url("http://example.test:8000/"),
            "http://example.test:8000"
        );
    }
}
