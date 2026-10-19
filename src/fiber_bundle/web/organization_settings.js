import { joinText, sourceText, setText, uiText } from "./i18n/index.js";
// Persistent policy and expert-package forms; saving remains explicitly owned by the caller.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
const options = (entries, current) => {
  const select = el("select");
  for (const [value, text] of entries) {
    const option = el("option", uiText(text));
    option.value = value;
    select.append(option);
  }
  select.value = current;
  return select;
};
const field = (label, input) => {
  const host = el("label", uiText(label));
  host.append(input);
  return host;
};

export function organizationSettings(result, fixed, api, work, callbacks) {
  const { onDirty, onClean, refresh } = callbacks;
  const settings = result.policy.settings,
    config = el("details"),
    summary = el("summary", uiText("条件策略与专家包接入"));
  const mode = options(
    [
      ["off", "关闭"],
      ["suggest", "仅建议"],
      ["automatic", "自动应用高置信结果"],
    ],
    settings.mode,
  );
  const include = el("input"),
    exclude = el("input");
  include.value = settings.include.join(", ");
  exclude.value = settings.exclude.join(", ");
  const interval = el("input");
  interval.type = "number";
  interval.min = "30";
  interval.value = settings.interval_seconds;
  const periodic = el("input"),
    full = el("input");
  periodic.type = full.type = "checkbox";
  periodic.checked = settings.periodic;
  full.checked = settings.full_review;
  const match = options(
    [
      ["all", "全部满足"],
      ["any", "任一满足"],
    ],
    settings.match,
  );
  const conditionKind = options(
    [
      ["tag", "标签"],
      ["title", "标题包含"],
      ["body", "正文包含"],
      ["tag_prefix", "标签前缀"],
      ["source", "来源类型"],
      ["kind", "笔记/专家包"],
      ["status", "归类状态"],
    ],
    settings.conditions[0]?.field || "tag",
  );
  const conditionValue = el("input");
  conditionValue.value = settings.conditions[0]?.values.join(",") || "";
  const ruleMoc = options(
    [
      ["", "不新增规则"],
      ...result.mocs.map((m) => [m.id, sourceText(m.title)]),
    ],
    "",
  );
  const ruleTag = el("input");
  setText(ruleTag, uiText("明确标签匹配此 MOC"), "placeholder");
  config.append(
    summary,
    field("执行方式", mode),
    field("允许目录（逗号分隔，留空为当前授权范围）", include),
    field("排除目录（逗号分隔）", exclude),
    field("条件组合", match),
    field("条件字段", conditionKind),
    field("条件值（逗号分隔，留空为无附加条件）", conditionValue),
    field("定期检查补漏", periodic),
    field("检查间隔（秒）", interval),
    field("允许定期复核授权范围的历史资料", full),
    field("添加明确分类规则", ruleMoc),
    field("规则标签", ruleTag),
  );
  const retainedRules = settings.rules.map((rule) => {
    const keep = el("input");
    keep.type = "checkbox";
    keep.checked = true;
    config.append(
      field(
        "保留规则：" +
          (result.mocs.find((m) => m.id === rule.moc_id)?.title ||
            rule.moc_id) +
          " · " +
          rule.conditions
            .map((c) => c.field + ":" + c.values.join(","))
            .join("；"),
        keep,
      ),
    );
    return keep;
  });
  config.addEventListener("input", () => {
    onDirty();
  });
  config.append(
    control("保存归类策略", () =>
      work(async () => {
        const split = (value) =>
          value
            .split(/[\n,，]/)
            .map((s) => s.trim())
            .filter(Boolean);
        const conditions = conditionValue.value.trim()
          ? [
              {
                field: conditionKind.value,
                values: split(conditionValue.value),
              },
            ]
          : [];
        if (settings.conditions.length > 1)
          conditions.push(...settings.conditions.slice(1));
        const rules = settings.rules.filter((_, i) => retainedRules[i].checked);
        if (ruleMoc.value && ruleTag.value.trim())
          rules.push({
            moc_id: ruleMoc.value,
            match: "all",
            conditions: [{ field: "tag", values: split(ruleTag.value) }],
          });
        await api.policy({
          ...fixed,
          expected_revision: result.policy.revision,
          settings: {
            ...settings,
            mode: mode.value,
            include: split(include.value).length ? split(include.value) : [""],
            exclude: split(exclude.value),
            match: match.value,
            conditions,
            rules,
            periodic: periodic.checked,
            full_review: full.checked,
            interval_seconds: Number(interval.value),
          },
        });
        onClean();
      }),
    ),
    control("放弃本页配置草稿", async () => {
      onClean();
      await refresh();
    }),
  );
  config.append(
    el("h3", uiText("Book 专家包")),
    el(
      "p",
      uiText(
        "后台自动扫描已配置的 Book，校验后整包登记；包内 Markdown 不参与普通笔记整理。登记后到 MOC 管理，可直接多选增挂，无需关闭自动归类。",
      ),
    ),
    control("扫描 Book 并登记整包", () => work(() => api.scan(fixed))),
  );
  const scan = result.package_scan;
  if (scan) {
    config.append(
      el(
        "p",
        uiText(
          scan.state === "queued"
            ? "专家包扫描已排队"
            : scan.state === "failed"
              ? "专家包扫描失败"
              : scan.state === "ready"
                ? "专家包扫描完成"
                : "尚未扫描 Book",
        ),
      ),
    );
    if (scan.error) config.append(el("p", sourceText(scan.error)));
    for (const item of scan.items || []) {
      config.append(
        el(
          "p",
          joinText([
            item.title || item.path,
            " · ",
            uiText(
              item.state === "registered" ? "已登记整包" : "整包校验未通过",
            ),
            item.error ? " · " + item.error : "",
          ]),
        ),
      );
    }
  }
  const path = el("input");
  setText(path, uiText("例如 04-Book/fake-heat-conduction-ch1"), "placeholder");
  config.append(
    field("专家 Skill 相对目录", path),
    control("登记专家包（保留原包）", () =>
      work(() => api.register({ ...fixed, path: path.value.trim() })),
    ),
  );
  config.open = true;
  return config;
}
