import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ReadinessReport } from "@/lib/health";

import { StatusView } from "./status-view";

const ready: ReadinessReport = {
  status: "ready",
  database: {
    reachable: true,
    server_version: "18.6",
    migration_revision: "0001",
    expected_revision: "0001",
    extensions: [
      { name: "vector", installed_version: "0.8.7" },
      { name: "pg_textsearch", installed_version: "1.5.1" },
    ],
    problems: [],
  },
  models: {
    llm_provider: "fake",
    embedder: "fake",
    embedding_dimensions: 1024,
    problems: [],
    warnings: [],
  },
};

describe("StatusView", () => {
  it("shows every check passing when the API is ready", () => {
    render(
      <StatusView
        result={{ kind: "report", httpStatus: 200, report: ready }}
      />,
    );

    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Postgres 18.6")).toBeInTheDocument();
    expect(screen.getByText("1.5.1")).toBeInTheDocument();
    expect(screen.queryAllByLabelText("failing")).toHaveLength(0);
  });

  it("lists the problems with commands shown as code", () => {
    const notReady: ReadinessReport = {
      ...ready,
      status: "not_ready",
      database: {
        ...ready.database,
        migration_revision: null,
        extensions: [{ name: "vector", installed_version: null }],
        problems: [
          "Extension `vector` is not installed. Run `uv run alembic upgrade head`.",
        ],
      },
    };

    render(
      <StatusView
        result={{ kind: "report", httpStatus: 503, report: notReady }}
      />,
    );

    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Not installed")).toBeInTheDocument();
    expect(screen.getByText("uv run alembic upgrade head").tagName).toBe(
      "CODE",
    );
    expect(screen.getAllByLabelText("failing")).toHaveLength(2);
  });

  it("shows an embedding model problem and warnings about chunks", () => {
    const loading: ReadinessReport = {
      ...ready,
      status: "not_ready",
      models: {
        ...ready.models,
        problems: ["The embedding model is still loading (seconds)."],
        warnings: ["3 chunks have no vector from this model."],
      },
    };

    render(
      <StatusView
        result={{ kind: "report", httpStatus: 503, report: loading }}
      />,
    );

    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(
      screen.getByText("The embedding model is still loading (seconds)."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("3 chunks have no vector from this model."),
    ).toBeInTheDocument();
    expect(screen.getAllByLabelText("failing")).toHaveLength(1);
  });

  it("explains how to start the API when it cannot be reached", () => {
    render(
      <StatusView
        result={{
          kind: "unreachable",
          apiUrl: "http://127.0.0.1:8000",
          message: "Cannot reach the API. Start it with `uv run fastapi dev`.",
        }}
      />,
    );

    expect(screen.getByText("Unreachable")).toBeInTheDocument();
    expect(screen.getByText("uv run fastapi dev")).toBeInTheDocument();
  });
});
