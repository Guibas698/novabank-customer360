name := "novabank-entity-resolution"
version := "0.1.0"
scalaVersion := "2.12.18"

val sparkVersion = "3.5.1"

libraryDependencies ++= Seq(
  "org.apache.spark" %% "spark-core" % sparkVersion % "provided",
  "org.apache.spark" %% "spark-sql" % sparkVersion % "provided",
  "org.apache.spark" %% "spark-graphx" % sparkVersion % "provided",
  "io.delta" %% "delta-spark" % "3.2.0" % "provided",
  "org.scalatest" %% "scalatest" % "3.2.18" % Test
)
