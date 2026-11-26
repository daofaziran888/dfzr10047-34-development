<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">解构工坊 · 异构文件解析与字段抽取编排平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向异构文件投递、格式探测、编码归一、解析器路由、抽取模板版本、字段解析、坏行隔离、结构嗅探、嵌套展平、取值转型、单位归一、输出契约、结果投递、解析水位与重解析编排的数据采集接入平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "坏行复核清单", path: "/quarantine-review" }, { label: "文件投递", path: "/file_intake" }, { label: "格式探测", path: "/format_probe" }, { label: "编码归一", path: "/encoding_norm" }, { label: "解析路由", path: "/parser_route" }, { label: "抽取模板", path: "/extract_tmpl" }, { label: "字段解析", path: "/field_parse" }, { label: "坏行隔离", path: "/row_quarantine" }, { label: "解析批次", path: "/parse_batch" }, { label: "结构嗅探", path: "/schema_sniff" }, { label: "分隔规则", path: "/delimiter_rule" }, { label: "嵌套展平", path: "/nested_flatten" }, { label: "取值转型", path: "/value_cast" }, { label: "单位归一", path: "/unit_normalize" }, { label: "输出契约", path: "/output_contract" }, { label: "结果投递", path: "/result_deliver" }, { label: "解析水位", path: "/parse_watermark" }, { label: "重解析任务", path: "/reparse_task" }, { label: "重复指纹", path: "/dup_fingerprint" }, { label: "抽样复核", path: "/sample_review" }, { label: "解析指标", path: "/parse_metric" }, { label: "模板差异", path: "/tmpl_diff" }, { label: "投递审计", path: "/intake_audit" }]
</script>
