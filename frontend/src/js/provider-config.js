export const DEFAULT_OCR_PROVIDER = "mineru_local";

export const OCR_PROVIDER_DEFINITIONS = [
  {
    id: "mineru_local",
    label: "MinerU Local",
    description: "使用本机或内网 MinerU 服务进行 OCR 和版面识别。",
    tokenField: "",
    runtimeConfigKey: "",
    tokenLabel: "",
    tokenPlaceholder: "",
    validationButtonLabel: "检测本地 MinerU",
    validationIdleMessage: "检测本机或内网 MinerU 服务是否可用。",
    validationMissingMessage: "",
    validationUnavailableMessage: "",
    docsUrl: "https://opendatalab.github.io/MinerU/usage/quick_usage/",
    docsLabel: "查看文档",
    supportsValidation: true,
    requiresToken: false,
  },
  {
    id: "paddle",
    label: "PaddleOCR",
    description: "用于 PaddleOCR 在线 OCR 解析。",
    tokenField: "paddle_token",
    runtimeConfigKey: "paddleToken",
    tokenLabel: "Paddle Access Token",
    tokenPlaceholder: "填写 Paddle Access Token",
    validationButtonLabel: "检测 Paddle",
    validationIdleMessage: "可检测 Paddle Access Token 是否可用。",
    validationMissingMessage: "请先填写 Paddle Access Token。",
    validationUnavailableMessage: "",
    docsUrl: "https://aistudio.baidu.com/account/accessToken",
    docsLabel: "获取 Token",
    supportsValidation: true,
    requiresToken: true,
  },
  {
    id: "mineru",
    label: "MinerU",
    description: "用于 OCR 解析和版面识别。",
    tokenField: "mineru_token",
    runtimeConfigKey: "mineruToken",
    tokenLabel: "MinerU Token",
    tokenPlaceholder: "填写 MinerU Token",
    validationButtonLabel: "检测 MinerU",
    validationIdleMessage: "保存前会自动检测 MinerU Token。",
    validationMissingMessage: "请先填写 MinerU Token。",
    validationUnavailableMessage: "",
    docsUrl: "https://mineru.net/apiManage/docs?openApplyModal=true",
    docsLabel: "获取 Token",
    supportsValidation: true,
    requiresToken: true,
  },
];

export const TRANSLATION_PROVIDER_DEFINITION = {
  id: "openai_compatible",
  label: "OpenAI Compatible",
  keyLabel: "模型 API Key",
  keyPlaceholder: "填写模型 API Key",
  description: "用于正文翻译和模型调用。",
  docsUrl: "https://platform.openai.com/docs/api-reference/chat",
  docsLabel: "协议说明",
  validationButtonLabel: "检测模型服务",
  validationIdleMessage: "可检测 OpenAI-compatible 接口是否连通。",
  validationMissingMessage: "请先填写模型 API Key。",
  validationSuccessMessage: "模型服务接口连接成功。",
  validationNetworkMessage: "模型服务接口检测失败，请检查 Base URL、Key 或网络。",
  validationUnauthorizedMessage: "模型 API Key 无效或已过期。",
};

export function normalizeOcrProvider(value) {
  const provider = `${value || ""}`.trim().toLowerCase();
  return OCR_PROVIDER_DEFINITIONS.some((item) => item.id === provider) ? provider : DEFAULT_OCR_PROVIDER;
}

export function getOcrProviderDefinition(provider) {
  return OCR_PROVIDER_DEFINITIONS.find((item) => item.id === normalizeOcrProvider(provider)) || OCR_PROVIDER_DEFINITIONS[0];
}
